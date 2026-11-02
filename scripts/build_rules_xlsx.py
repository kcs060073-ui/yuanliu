# -*- coding: utf-8 -*-
"""生成《语义规则分配表.xlsx》——园区素材分配规则的唯一编辑入口。

用法：
    python build_rules_xlsx.py [输出路径]

⚠️ 保留人工标注：重复运行时会把已有表格里**你改过的**内容原样保留
   （语义规则分配表的 可跨园区通用 / 通用例外 / 回退 / 备注，
    园区识别词表的 显示名 / 识别关键词 / 备注），只刷新自动统计部分
   （园区素材覆盖矩阵）。所以随时可以重跑，不会吃掉你的标注。

表格结构：
  Sheet1 语义规则分配表   ← 改 G/H 列（桶能否跨园区通用）+ D 列（素材侧关键词）
  Sheet2 园区素材覆盖矩阵 ← 自动统计，用来判断"哪些桶该开放通用"
  Sheet3 园区识别词表     ← 脚本里出现哪个地名 → 用哪个素材文件夹；E 列邻近园区
  Sheet4 使用手册         ← 全部使用方式与规则

改完 Excel 后运行 import_rules.py 让配置生效。
"""
import os
import sys
import json
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.worksheet.datavalidation import DataValidation

import material_matcher as mm

MATERIALS_ROOT = 'E:/园区素材'

BUCKET_CN = {
    'entrance': '园区大门/入口',
    'aerial': '航拍/全景',
    'railway': '高铁/火车站',
    'metro': '地铁',
    'traffic': '道路交通（含高速/公路）',
    'inside': '厂房内部',
    'factory': '厂房/厂区（外部）',
    'dormitory': '宿舍',
    'canteen': '食堂/餐厅',
    'office': '办公楼/写字楼',
    'environment': '配套/环境',
}

BUCKET_BLOCK = {
    'entrance': 'opening',
    'aerial': 'opening',
    'railway': 'location',
    'metro': 'location',
    'traffic': 'location',
    'inside': 'interior / interior_spec / interior_area / interior_struct / price',
    'factory': 'exterior / price / policy / cta',
    'dormitory': 'support',
    'canteen': 'support',
    'office': 'support',
    'environment': 'support',
}

# 默认可跨园区通用规则（2026-09-11 用户拍板）：
#   1. 所有园区的「厂房内部」可以通用
#   2. 「宿舍」可以通用，但排除新疆园区
DEFAULT_SHARE = {
    'inside': ('是', '', '借用其他园区厂房内部'),
    'dormitory': ('是', '排除 新疆园区', '借用其他园区宿舍（不含新疆）'),
}

ALIAS_DEFAULT = {
    '北京园区': ('北京', '北京,医械城', ['德州园区'], ''),
    '嘉兴园区': ('嘉兴', '嘉兴', ['新仓园区', '嘉定园区', '苏州园区'], ''),
    '新疆园区': ('两河园区', '乌鲁木齐,新疆,两河园区,两河', [], '宿舍素材不通用（观众能认出）'),
    '苏州园区': ('苏州', '苏州', ['嘉定园区', '嘉兴园区', '新仓园区', '南京园区'], ''),
    '贵阳园区': ('贵阳', '贵阳,贵州', [], ''),
    # 2026-09-14 新增四园区（铠畅放入素材库后自动登记）
    # 注意：新仓刻意不用光秃秃的"新仓" —— 脚本里"新仓库/新建仓库"会误命中。
    '南京园区': ('南京', '南京,南京园区', ['苏州园区', '嘉定园区', '德州园区'], ''),
    '嘉定园区': ('嘉定', '嘉定,上海嘉定,嘉定园区', ['苏州园区', '嘉兴园区', '新仓园区'],
                 '独立园区，不再并入嘉兴'),
    '德州园区': ('德州', '德州,山东德州,德州园区', ['北京园区', '南京园区'], ''),
    '新仓园区': ('新仓', '新仓园区,平湖新仓,新仓出口', ['嘉兴园区', '嘉定园区', '苏州园区'],
                 '不用单独"新仓"（会命中"新仓库"）；用"新仓出口"这类明确写法'),
}

HDR_FILL = PatternFill('solid', fgColor='DDEBF7')
EDIT_FILL = PatternFill('solid', fgColor='FFF2CC')
KEEP_FILL = PatternFill('solid', fgColor='E2F0D9')
THIN = Side(style='thin', color='BFBFBF')
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _norm(v):
    return ('' if v is None else str(v)).strip()


def _split_kws(text):
    """把表里的关键词串拆成列表（支持「、」「，」「,」和空格分隔）。"""
    t = (text or '').replace('、', ',').replace('，', ',').replace('；', ',') \
                   .replace(';', ',').replace('/', ',')
    return [x.strip() for x in t.split(',') if x.strip()]


def _scan_coverage():
    """统计每个园区目录下各语义桶的视频数量。"""
    cov = {}
    if not os.path.isdir(MATERIALS_ROOT):
        return cov
    for d in sorted(os.listdir(MATERIALS_ROOT)):
        p = os.path.join(MATERIALS_ROOT, d)
        if not os.path.isdir(p):
            continue
        try:
            items = mm.scan_materials(p, with_duration=False)
        except Exception:
            items = []
        cnt = {}
        for it in items:
            b = it.get('bucket')
            cnt[b] = cnt.get(b, 0) + 1
        cov[d] = cnt
    return cov


def load_existing(path):
    """读取已有表格里的人工标注（重跑时保留，不覆盖）。

    ⚠️ 文件存在但**读不出来**（最常见：表格正在 Excel 里打开）时**直接报错停止**，
    不再当成"首次生成"继续跑 —— 否则会静默覆盖掉用户已标注的内容。
    2026-09-11 实测踩过：文件被 Excel 占用 → 读取异常被吞 → 标注被默认值冲掉。
    """
    if not os.path.isfile(path):
        return {'buckets': {}, 'parks': {}}
    try:
        wb = load_workbook(path, data_only=True)
    except Exception as e:
        raise RuntimeError(
            '已存在的表格读取失败：%s\n'
            '  原因：%s\n'
            '  常见情况：表格正在 Excel 里打开，文件被占用。\n'
            '  为免覆盖你已标注的内容，本次**没有写入任何文件**。'
            '请关掉 Excel 后重跑。' % (path, e))
    buckets, parks = {}, {}
    if '语义规则分配表' in wb.sheetnames:
        for r in wb['语义规则分配表'].iter_rows(min_row=2, values_only=True):
            key = _norm(r[1]) if len(r) > 1 else ''
            if not key:
                continue
            buckets[key] = {'share': _norm(r[6]) if len(r) > 6 else '',
                            'exc': _norm(r[7]) if len(r) > 7 else '',
                            'fallback': _norm(r[8]) if len(r) > 8 else '',
                            'note': _norm(r[9]) if len(r) > 9 else '',
                            'kws': _norm(r[3]) if len(r) > 3 else ''}
    if '园区识别词表' in wb.sheetnames:
        ws3 = wb['园区识别词表']
        hdr = [_norm(c) for c in next(ws3.iter_rows(min_row=1, max_row=1, values_only=True), ())]
        # 兼容两版布局：老版 5 列（备注在第5列），新版 6 列（第5列邻近园区、第6列备注）
        note_i = 5 if len(hdr) >= 6 else 4
        near_i = 4 if len(hdr) >= 6 else None
        for r in ws3.iter_rows(min_row=2, values_only=True):
            folder = _norm(r[0]) if r else ''
            if not folder:
                continue
            nearby = []
            if near_i is not None and len(r) > near_i:
                nearby = [_norm(x) for x in _norm(r[near_i]).replace('，', ',')
                          .replace('、', ',').split(',') if _norm(x)]
            parks[folder] = {'display': _norm(r[1]) if len(r) > 1 else '',
                             'kws': _norm(r[2]) if len(r) > 2 else '',
                             'priority': _norm(r[3]) if len(r) > 3 else '',
                             'nearby': nearby,
                             'note': _norm(r[note_i]) if len(r) > note_i else ''}
    return {'buckets': buckets, 'parks': parks}


def build(out_path, cov=None, config_wins=False):
    cov = cov if cov is not None else _scan_coverage()
    old = load_existing(out_path)
    parks_found = sorted(cov.keys())
    buckets = [b for b in mm.BUCKET_ORDER if b in BUCKET_CN]

    wb = Workbook()
    kept_b, kept_p = [], []

    # ---- D 列（素材侧关键词）方向判定 ----
    # 配置(config/semantics.json)与表格 D 列都能表达同一件事，必须明确"谁说了算"：
    #   默认：表里改了还没导入 → **拦停**，提示先导入（否则会静默覆盖你的改动）
    #   --config-wins：以配置为准刷新 D 列（AI 改完配置后用这个）
    drift = []
    for b in buckets:
        cfg_kws = list(mm.SEMANTIC_BUCKETS.get(b, []))
        prev = old['buckets'].get(b)
        if not prev:
            continue
        table_kws = _split_kws(prev.get('kws'))
        if table_kws != cfg_kws:
            drift.append((b, cfg_kws, table_kws))
    if drift and not config_wins:
        msg = ['表格 D 列（素材侧关键词）与 config/semantics.json 不一致，已停止生成：']
        for b, cfg_kws, table_kws in drift:
            add = [k for k in table_kws if k not in cfg_kws]
            rm = [k for k in cfg_kws if k not in table_kws]
            msg.append('  %s：表里新增 %s；表里删除 %s' % (b, add or '无', rm or '无'))
        msg.append('两种走法，请选一个：')
        msg.append('  ① 表里的改动是要生效的 → 先跑 import_rules.py --keywords-from-table 导入')
        msg.append('  ② 以配置为准、丢弃表里的改动 → 加 --config-wins 重跑本脚本')
        raise RuntimeError('\n'.join(msg))

    # ---------------- Sheet1 语义规则分配表 ----------------
    ws = wb.active
    ws.title = '语义规则分配表'
    headers = ['序号', '语义桶(key)', '中文名', '素材侧关键词（仅目录命中）',
               '脚本侧语义块', '本园区优先', '可跨园区通用', '通用例外',
               '缺素材时回退', '备注（请在此标注）']
    ws.append(headers)
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=i)
        c.font = Font(bold=True)
        c.fill = HDR_FILL
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        c.border = BORDER

    rows = [(b, BUCKET_CN.get(b, b)) for b in buckets] + [(None, '未命中任何语义词')]
    for idx, (b, cn) in enumerate(rows, 1):
        key = b if b else '(未分类)'
        share, exc, fallback = DEFAULT_SHARE.get(b, ('否', '', '复用相邻段落画面（不跨园区借用）'))
        note = ''
        prev = old['buckets'].get(key)
        if prev:
            if prev['share'] in ('是', '否'):
                share = prev['share']
            exc = prev['exc']
            fallback = prev['fallback'] or fallback
            note = prev['note']
            kept_b.append(key)
        ws.append([idx, key, cn,
                   '、'.join(mm.SEMANTIC_BUCKETS.get(b, [])) if b else '',
                   BUCKET_BLOCK.get(b, ''), '是', share, exc, fallback, note])

    dv = DataValidation(type='list', formula1='"是,否"', allow_blank=False)
    ws.add_data_validation(dv)
    dv.add('G2:G%d' % (len(rows) + 1))

    widths = [6, 14, 18, 34, 30, 10, 14, 18, 26, 34]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + i)].width = w
    for r in range(2, len(rows) + 2):
        for c in range(1, len(headers) + 1):
            cell = ws.cell(row=r, column=c)
            cell.border = BORDER
            cell.alignment = Alignment(vertical='center', wrap_text=True)
        for c in (7, 8, 10):
            ws.cell(row=r, column=c).fill = EDIT_FILL
    ws.freeze_panes = 'C2'

    # ---------------- Sheet2 园区素材覆盖矩阵 ----------------
    ws2 = wb.create_sheet('园区素材覆盖矩阵')
    ws2.append(['语义桶', '中文名'] + parks_found + ['合计'])
    for i in range(len(parks_found) + 3):
        c = ws2.cell(row=1, column=i + 1)
        c.font = Font(bold=True)
        c.fill = HDR_FILL
        c.alignment = Alignment(horizontal='center', wrap_text=True)
        c.border = BORDER
    for b in buckets + [None]:
        row = [b if b else '(未分类)', BUCKET_CN.get(b, '未命中')]
        total = 0
        for p in parks_found:
            n = cov.get(p, {}).get(b, 0)
            total += n
            row.append(n)
        row.append(total)
        ws2.append(row)
    ws2.column_dimensions['A'].width = 14
    ws2.column_dimensions['B'].width = 18
    for i in range(len(parks_found)):
        ws2.column_dimensions[chr(67 + i)].width = 12
    for r in range(2, len(buckets) + 3):
        for c in range(1, len(parks_found) + 4):
            ws2.cell(row=r, column=c).border = BORDER
            ws2.cell(row=r, column=c).alignment = Alignment(horizontal='center')
    ws2.freeze_panes = 'C2'

    # ---------------- Sheet3 园区识别词表 ----------------
    ws3 = wb.create_sheet('园区识别词表')
    h3 = ['素材文件夹', '显示名', '脚本识别关键词（逗号分隔）', '命中优先级',
          '邻近园区（逗号分隔，借用时优先从这里取）', '备注']
    ws3.append(h3)
    for i in range(len(h3)):
        c = ws3.cell(row=1, column=i + 1)
        c.font = Font(bold=True)
        c.fill = HDR_FILL
        c.alignment = Alignment(horizontal='center', wrap_text=True)
        c.border = BORDER
    # 已存在的园区（含已撤销目录的旧行）保留；新出现的目录自动补一行
    order = []
    for f in list(old['parks'].keys()) + parks_found:
        if f not in order:
            order.append(f)
    for p in order:
        disp, kws, nearby, note = ALIAS_DEFAULT.get(
            p, (p.replace('园区', ''), p.replace('园区', ''), [], ''))
        prev = old['parks'].get(p)
        if prev:
            disp = prev['display'] or disp
            kws = prev['kws'] or kws
            nearby = prev.get('nearby') or nearby
            note = prev['note'] or note
            kept_p.append(p)
        pri = _norm(prev.get('priority')) if prev else ''
        pri = int(pri) if pri.isdigit() else 1
        ws3.append([p, disp, kws, pri, ','.join(nearby), note])
    for i, w in enumerate([16, 14, 40, 12, 34, 30], 1):
        ws3.column_dimensions[chr(64 + i)].width = w
    for r in range(2, len(order) + 2):
        for c in range(1, len(h3) + 1):
            ws3.cell(row=r, column=c).border = BORDER
            ws3.cell(row=r, column=c).alignment = Alignment(vertical='center', wrap_text=True)
        for c in (3, 5, 6):
            ws3.cell(row=r, column=c).fill = EDIT_FILL
    ws3.freeze_panes = 'A2'

    # ---------------- Sheet4 使用手册 ----------------
    ws4 = wb.create_sheet('使用手册')
    lines = [
        ['源流园区短视频 · 素材分配规则 使用手册', ''],
        ['', '本表是素材分配规则的**唯一人工编辑入口**。改完发我导入即可生效。'],
        ['', ''],
        ['一、这套规则在做什么', ''],
        ['背景', '2026-09-11 贵阳片出事故：出片时把素材总根当素材目录，5 个园区 900 条'],
        ['', '素材混成一个大池，贵阳只占 4.6%，7 段画面全被新疆/北京/嘉兴抽走，'],
        ['', '其中 4 段是乌鲁木齐宿舍 —— 而画面本身没有任何瑕疵，光看片子发现不了。'],
        ['现在的规则', '① 先按脚本里的地名词锁定园区，只在该园区文件夹里随机选；'],
        ['', '② 只有本表标了"可跨园区通用"的桶，才允许向其他园区借；'],
        ['', '③ 借用时先从"邻近园区"取；'],
        ['', '④ 识别不到园区就直接拦停，不再退化成多园区混池；'],
        ['', '⑤ 每次出片自动做"素材来源审计"，越界就报 FAIL。'],
        ['', ''],
        ['二、日常出片（正常做视频）', ''],
        ['你只需要', '把脚本给我（脚本里写明园区地名），我跑全链路并回你结果'],
        ['我会做', '锁定园区 → 选素材 → 生成草稿 → 校验（含来源审计）→ 回加密 → 放剪映草稿库'],
        ['', ''],
        ['三、改规则①：素材侧关键词（本表 D 列，支持双向编辑）', ''],
        ['用途', '决定"哪个目录名 / 文件名算哪个语义桶"，比如给交通加一个"马路"'],
        ['怎么写', 'D 列用「、」或逗号分隔，例如：交通、路口、道路、公路、马路'],
        ['第 1 步', '在「语义规则分配表」D 列增删词'],
        ['第 2 步', '保存关闭 Excel，发我；我跑：python scripts/import_rules.py --keywords-from-table'],
        ['第 3 步', '我会回显 diff（哪个桶加了哪些词、删了哪些词）并跑语义回归'],
        ['第 4 步', '出一条验证片确认画面符合预期'],
        ['⚠️ 重要', 'D 列与配置文件不一致时，重新生成表格会**停下来报错**，不会静默覆盖。'],
        ['', '两种走法（脚本会提示）：① 表里的改动要生效 → 先导入（--keywords-from-table）；'],
        ['', '② 以配置为准、丢弃表的改动 → 加 --config-wins 重跑生成。'],
        ['⚠️ 风险', '关键词写错**不会报错**，只会让素材悄悄落进兜底池（成片看不出）。'],
        ['', '所以每次导入我都会回显 diff，请核对一遍。'],
        ['', ''],
        ['四、改规则②：桶能不能跨园区借（G / H 列）', ''],
        ['G 可跨园区通用', '是 = 本园区没有该桶素材时，可以借其他园区的；否 = 只能用本园区'],
        ['H 通用例外', '该桶可通用的前提下，**不从哪些园区借**。写来源园区文件夹名，'],
        ['', '多个用逗号分隔，例如：排除 新疆园区,嘉兴园区'],
        ['', '（注意：不是"本园区不能用这个桶"，而是"不从这些园区借"）'],
        ['第 1 步', '改 G / H 列 → 保存关闭 → 发我 → 我跑 python scripts/import_rules.py'],
        ['', ''],
        ['五、改规则③：新增园区', ''],
        ['第 1 步', '在 E:/园区素材 下新建园区文件夹（如 嘉定园区），把素材放进去'],
        ['第 2 步', '让我重跑生成：python scripts/build_rules_xlsx.py'],
        ['', '新园区会自动出现在「园区识别词表」和「园区素材覆盖矩阵」里'],
        ['第 3 步', '在「园区识别词表」填 C 列脚本识别关键词（如 嘉定,上海嘉定,嘉定园区）'],
        ['第 4 步', '填 E 列邻近园区（借用时优先从这些园区取）'],
        ['第 5 步', 'D 列命中优先级：数字小的园区优先（默认 1）。仅当一条脚本同时命中'],
        ['', '多个园区且命中词长度相同时才起作用 —— 见第七节"多园区同时命中怎么判"'],
        ['第 5 步', '发我导入：python scripts/import_rules.py'],
        ['注意', '园区文件夹不存在时导入会被拒绝，防止名字打错的园区混进规则'],
        ['注意', '脚本识别关键词只认 C 列。园区"显示名"不参与脚本匹配 ——'],
        ['', '否则"新仓"会把脚本里的"全**新仓**库""新仓库"误判成园区名。'],
        ['', ''],
        ['六、改规则④：多园区同时命中怎么判（2026-09-14 新增）', ''],
        ['场景', '脚本里同时出现"嘉兴""新仓出口"这类上下级地名时，该用哪个园区的素材'],
        ['规则 1', '**更具体的词优先**：命中词越长越优先。"新仓出口"(4字) 赢过 "嘉兴"(2字)。'],
        ['规则 2', '同长度再看「命中优先级」列（D 列，数字小优先，默认 1）。'],
        ['规则 3', '再看出现位置（最早优先）。'],
        ['规则 4', '**界面/命令行填的园区名优先于脚本推断** —— 你填了园区名就按你填的锁定，'],
        ['', '若与脚本推断不一致，出片日志会打印醒目提示，请核对'],
        ['举例', '本次新仓事故：脚本写"嘉兴平湖""沈海高速新仓出口"，旧规则取最早的"嘉兴"→'],
        ['', '锁成嘉兴园区。现在：(a) 界面填"新仓"直接生效；(b) 即使不填，"新仓出口"'],
        ['', '比"嘉兴"长也会判给新仓'],
        ['', ''],
        ['七、改规则⑤：邻近园区（E 列）', ''],
        ['用途', '允许借用的桶，先从邻近园区取（不放开新桶，只降低穿帮概率：'],
        ['', '长三角几家的宿舍楼、办公楼彼此更像）'],
        ['当前默认', '北京↔德州；德州↔北京/南京；南京↔苏州/嘉定/德州；'],
        ['', '苏州↔嘉定/嘉兴/新仓/南京；嘉定↔苏州/嘉兴/新仓；'],
        ['', '嘉兴↔新仓/嘉定/苏州；新仓↔嘉兴/嘉定/苏州；贵阳、新疆留空（地理孤立）'],
        ['', ''],
        ['七、列含义（★=手工编辑有效，○=自动生成，改了没用）', ''],
        ['★ 语义规则分配表 G', '可跨园区通用：是 / 否'],
        ['★ 语义规则分配表 H', '通用例外：写不从哪些园区借'],
        ['★ 语义规则分配表 D', '素材侧关键词（双向编辑，见第三节）'],
        ['★ 语义规则分配表 J', '备注（只做记录，不影响出片）'],
        ['★ 园区识别词表 C', '脚本识别关键词'],
        ['★ 园区识别词表 E', '邻近园区'],
        ['○ 语义规则分配表 E', '脚本侧语义块（自动，对应哪一段用哪个桶）'],
        ['○ 语义规则分配表 F', '本园区优先（固定为"是"）'],
        ['○ 园区素材覆盖矩阵', '各园区各桶的素材条数，自动统计'],
        ['', ''],
        ['八、当前默认规则（2026-09-14）', ''],
        ['11 个语义桶', 'entrance 大门口 / aerial 航拍全景 / railway 高铁站 / metro 地铁 /'],
        ['', 'traffic 道路交通(含高速) / inside 厂房内部 / factory 厂房外部 /'],
        ['', 'dormitory 宿舍 / canteen 食堂 / office 办公 / environment 配套环境'],
        ['可跨园区通用', 'inside 厂房内部、dormitory 宿舍（排除新疆）、canteen 食堂、'],
        ['', 'office 办公、environment 配套环境 —— 缺哪个就从邻近园区借哪个（2026-09-14 起）'],
        ['不通用', 'factory 厂房外部（用户 2026-09-09 拍板的硬红线）、entrance 大门（有招牌）、'],
        ['', 'aerial 航拍（一眼看出园区布局）、railway/metro/traffic（站点与收费站带地名牌）'],
        ['为什么', '可借的都是"看不出是哪个园区"的镜头（餐盘、工位、绿化）；'],
        ['', '不借的都带园区地标，借了观众能认出来 —— 这正是当初定规则的原因'],
        ['想再放开', '直接在「可跨园区通用」列把对应行改成"是"即可（改完导入生效）'],
        ['素材类型', '**视频和图片都参与选片**（2026-09-15 起；原"图片不参与选片"作废）'],
        ['', '图片可用下限：短边 ≥ 540 像素、单边 ≤ 12000、长宽比 ≤ 4:1，'],
        ['', '太小会放大糊、维度异常多半是坏图 —— 这类不进池，其余正常参与'],
        ['', '使用约束（2026-09-15）：段落总时长 > 5s 时优先视频素材；同一段落最多 2 个图片素材'],
        ['素材归类', '**只看目录名**，文件名不参与判定（2026-09-14 起）'],
        ['', '目录没写语义的素材进"未分类池"，哪里缺补哪里'],
        ['', '以「园区/产业园」结尾的目录层级（如 新疆园区、两河园区）跳过不判'],
        ['命名约定', '0式 YYMMDD-城市-风格名；7式 YYMMDD-园区-横/竖-剪辑者'],
        ['横竖屏', '竖屏/竖版素材只用于竖版成片，横屏/横版只用于横版，无标注通用（CLI --orientation）'],
        ['', '方案 B（2026-09-15）：某语义桶在本园区**同方向素材为 0** 时，该桶回退用另一方向；'],
        ['', '同方向有素材的桶不受影响（裁切效果在剪映自行判断）'],
        ['语义判定', '含"宿舍/公寓/人才公寓/住宿"的句子**优先算生活配套**（2026-09-16 起），配宿舍/公寓画面'],
        ['', '否则"精装人才公寓月租500元起"会因带金额被判成价格句 → 画面配成厂房'],
        ['', ''],
        ['九、出片时的自动检查', ''],
        ['园区锁定', '脚本/--park/目录名任一能识别出园区就锁定；识别不到且素材目录挂着多个'],
        ['', '园区 → 直接拦停报错，绝不混用'],
        ['素材来源审计', '每次出片自动核对：本园区素材 → OK；借来的桶若规则开放且来源不在例外里'],
        ['', '→ OK 并标"合规借用"；其余 → FAIL 并指出越界的园区与桶'],
        ['', '只读草稿 JSON、不解码视频，约 0.1 秒；草稿目录会写 park_lock.json 留档'],
        ['', ''],
        ['十、出片完成提示与耗时（2026-09-15 新增）', ''],
        ['工具台', '出片结束后日志里打一条醒目横幅：✅/❌ + 总耗时 + 草稿目录 + 自检结果'],
        ['', '顶部状态栏同步显示「✅ 完成 · 耗时 X」；可勾选「完成后弹窗提醒」（默认开）'],
        ['批量出片', '结束时汇总「N/M 通过 · 总耗时」，并弹窗提示'],
        ['命令行', '同样在末尾打印「✅ 草稿生成完成 总耗时 X」；失败打印「❌ 未生成草稿 耗时 X」'],
        ['', ''],
        ['十一、命令速查（我这边执行，你一般不用敲）', ''],
        ['生成/刷新表格', 'python scripts/build_rules_xlsx.py [--config-wins]'],
        ['导入规则', 'python scripts/import_rules.py [--keywords-from-table]'],
        ['语义回归', 'python tests/test_semantic_blocks.py'],
        ['校验一条草稿', 'python tests/verify_draft.py "<草稿目录>"'],
        ['环境体检', 'python scripts/doctor.py'],
        ['', ''],
        ['十二、变更记录', ''],
        ['2026-09-11', '园区优先选片落地；来源审计；worksheet 归零'],
        ['2026-09-14 ①', '高速 highway 与公路 traffic 合并（用户：命名不规范，是同一个意思）'],
        ['2026-09-14 ②', 'support 块改为按句内实际词排序：食堂→canteen、办公→office、'],
        ['', '环境/绿化/广场/停车/车位/充电桩→environment，都没提到才回退宿舍'],
        ['2026-09-14 ③', '素材归类只用目录名；真航拍只认 全景/俯拍/航拍/鸟瞰'],
        ['', '（实测 118 条"航拍"里 103 条是文件名 DJI 判的，北京 92 条全在「专业设施」）'],
        ['2026-09-14 ④', '新增园区识别词 南京/嘉定/德州/新仓；新增邻近园区列'],
        ['2026-09-14 ⑤', 'D 列支持双向编辑（表 → 配置），带冲突拦停与 diff 回显'],
        ['2026-09-15 ①', '图片参与选片（可用下限 540px 等）+ 工具台/命令行出片完成提示与耗时'],
        ['2026-09-15 ②', '图片段落约束：>5s 段落优先视频素材、每段图片 ≤ 2 个'],
        ['2026-09-15 ③', '横竖屏"整桶回退"：某语义桶在本园区同方向素材为 0 时，该桶回退用另一方向'],
        ['2026-09-16 ①', '居住实体词（宿舍/公寓/人才公寓/住宿）优先于价格词判定，修掉"人才公寓月租500元起"被配成厂房素材'],
    ]
    for r in lines:
        ws4.append(r)
    ws4.column_dimensions['A'].width = 22
    ws4.column_dimensions['B'].width = 92
    for r in range(1, len(lines) + 1):
        ws4.cell(row=r, column=1).font = Font(bold=True)
        ws4.cell(row=r, column=2).alignment = Alignment(wrap_text=True, vertical='center')

    # 原子落盘（2026-09-15）：直接 wb.save(out_path) 在目标被 Excel/WPS 间歇占用时
    # 会抛 PermissionError（实测连续 20 次重试全失败），还可能写出半截文件。
    # 改为先写同目录临时文件，再 os.replace 原子替换；replace 撞锁则短暂重试。
    tmp = '%s.tmp-%d' % (out_path, os.getpid())
    wb.save(tmp)
    err = None
    for _ in range(10):
        try:
            os.replace(tmp, out_path)
            err = None
            break
        except OSError as e:
            err = e
            time.sleep(0.3)
    if err is not None:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise RuntimeError('目标表被占用，写入失败：%s（请关闭 Excel/WPS 后重试）' % out_path)
    return out_path, {'kept_buckets': kept_b, 'kept_parks': kept_p,
                      'config_wins': config_wins, 'drift': drift}


def main():
    argv = [a for a in sys.argv[1:]]
    config_wins = '--config-wins' in argv
    argv = [a for a in argv if not a.startswith('--')]
    out = argv[0] if argv else os.path.join(_ROOT, '语义规则分配表.xlsx')
    try:
        p, kept = build(out, config_wins=config_wins)
    except RuntimeError as e:
        print('[错误] %s' % e)
        sys.exit(2)
    print('已生成:', p)
    if kept['kept_buckets'] or kept['kept_parks']:
        print('已保留你的既有标注：')
        if kept['kept_buckets']:
            print('  语义规则 %d 行：%s' % (len(kept['kept_buckets']), '、'.join(kept['kept_buckets'])))
        if kept['kept_parks']:
            print('  园区识别 %d 行：%s' % (len(kept['kept_parks']), '、'.join(kept['kept_parks'])))
    if kept.get('config_wins'):
        print('  （--config-wins：D 列素材侧关键词已按 config/semantics.json 刷新）')
    if not (kept['kept_buckets'] or kept['kept_parks']):
        print('（首次生成，无既有标注可保留）')


if __name__ == '__main__':
    main()
