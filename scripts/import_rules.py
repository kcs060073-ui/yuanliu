# -*- coding: utf-8 -*-
"""把《语义规则分配表.xlsx》写回配置 —— 改完表格跑一次即可生效。

用法：
    python import_rules.py [xlsx路径]                      # 导入园区/桶规则
    python import_rules.py --keywords-from-table [xlsx]    # 额外把 D 列素材侧关键词写进 config/semantics.json

写两份配置：
  1. config/park_rules.json   ← 园区识别词、邻近园区、各桶能否跨园区通用
  2. config/semantics.json    ← 仅当 --keywords-from-table：素材侧关键词（D 列）

为什么关键词导入要显式加参数：D 列和 semantics.json 表达同一件事，默认以配置为准；
表里改了词必须显式声明"以表为准"，避免 AI 改完配置后被一张旧表悄悄覆盖回去。

读取：
  Sheet「语义规则分配表」  B列语义桶 / G列可跨园区通用 / H列通用例外
  Sheet「园区识别词表」    A列素材文件夹 / B列显示名 / C列脚本识别关键词

会先做校验（园区文件夹必须真实存在于素材根目录），校验不通过则**不写盘**并报错。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from openpyxl import load_workbook

import park_rules

DEFAULT_XLSX = os.path.join(_ROOT, '语义规则分配表.xlsx')
MATERIALS_ROOT = os.environ.get('MATERIALS_ROOT', 'E:/园区素材')


def _norm(v):
    return ('' if v is None else str(v)).strip()


def _split_kws(text):
    """关键词串拆列表（支持「、」「，」「,」「;」和空格分隔）。"""
    t = _norm(text).replace('、', ',').replace('，', ',').replace('；', ',').replace(';', ',')
    return [x.strip() for x in t.split(',') if x.strip()]


def import_keywords(data, path=None):
    """把表里 D 列的素材侧关键词写进 config/semantics.json。

    校验：桶必须存在于语义配置、关键词不能为空；先校验后写盘（校验不过不落盘）。
    """
    sys.path.insert(0, _HERE)
    import semantics_config as sc

    cfg = sc.load(path)
    sb = cfg['semantic_buckets']
    changed, skipped = [], []
    for key, info in data['buckets'].items():
        kws = info.get('keywords') or []
        if key == '(未分类)' or not kws:
            if not kws and key != '(未分类)':
                skipped.append((key, '表里关键词为空，跳过（空关键词 = 该桶永远命中不到）'))
            continue
        if key not in sb:
            skipped.append((key, '语义配置里没有这个桶（表可能过期，先重跑 build_rules_xlsx.py）'))
            continue
        old = list(sb[key])
        if old == kws:
            continue
        sb[key] = kws
        changed.append((key, [k for k in kws if k not in old], [k for k in old if k not in kws]))

    if not changed:
        print('  D 列素材侧关键词：与配置一致，无需改动')
        for k, why in skipped:
            print('    - %s：%s' % (k, why))
        return False

    try:
        sc.validate(cfg)
    except Exception as e:
        raise SystemExit('[错误] 关键词改动没通过语义配置校验，**未写盘**：\n  %s' % e)

    import shutil
    bak = sc.DEFAULT_PATH + '.bak-kw'
    shutil.copyfile(sc.DEFAULT_PATH, bak)
    with open(sc.DEFAULT_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)

    print('  D 列素材侧关键词已写入 %s（原文件备份 %s）' % (sc.DEFAULT_PATH, bak))
    for key, add, rm in changed:
        print('    - %-12s 新增 %s；删除 %s' % (key, add or '无', rm or '无'))
    for k, why in skipped:
        print('    - %s：%s' % (k, why))
    print('  改完请跑回归：python tests/test_semantic_blocks.py')
    return True


import json


def read_xlsx(path):
    wb = load_workbook(path, data_only=True)

    if '语义规则分配表' not in wb.sheetnames:
        raise SystemExit('[错误] 缺少工作表「语义规则分配表」')
    ws = wb['语义规则分配表']
    buckets = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        key = _norm(row[1])
        if not key or key == '(未分类)':
            continue
        share = _norm(row[6]) or '否'
        exc_raw = _norm(row[7])
        fallback = _norm(row[8])
        if share not in ('是', '否'):
            raise SystemExit('[错误] 第 G 列只能是 是/否，当前值：%r（桶 %s）' % (share, key))
        exclude = []
        if share == '是' and exc_raw:
            for word in exc_raw.replace('，', ',').replace('、', ',').split(','):
                w = _norm(word)
                for tail in ('排除', '不含', '除了'):
                    if w.startswith(tail):
                        w = _norm(w[len(tail):])
                if w:
                    exclude.append(w)
        buckets[key] = {'cross': share == '是', 'exclude': exclude, 'fallback': fallback,
                        'keywords': _split_kws(row[3] if len(row) > 3 else '')}

    if '园区识别词表' not in wb.sheetnames:
        raise SystemExit('[错误] 缺少工作表「园区识别词表」')
    ws3 = wb['园区识别词表']
    hdr = [_norm(c) for c in next(ws3.iter_rows(min_row=1, max_row=1, values_only=True), ())]
    near_i = 4 if len(hdr) >= 6 else None          # 老版 5 列没有邻近园区列
    parks = {}
    for row in ws3.iter_rows(min_row=2, values_only=True):
        folder = _norm(row[0])
        if not folder:
            continue
        display = _norm(row[1]) or folder.replace('园区', '')
        kws = [w for w in _norm(row[2]).replace('，', ',').replace('、', ',').split(',') if _norm(w)]
        if not kws:
            raise SystemExit('[错误] 园区 %s 没有填脚本识别关键词' % folder)
        pri_raw = _norm(row[3]) if len(row) > 3 else ''
        priority = int(pri_raw) if pri_raw.isdigit() else 1
        nearby = []
        if near_i is not None and len(row) > near_i:
            nearby = [w for w in _norm(row[near_i]).replace('，', ',').replace('、', ',')
                      .split(',') if _norm(w)]
        parks[folder] = {'display': display, 'aliases': kws,
                         'priority': priority, 'nearby': nearby}

    if not parks:
        raise SystemExit('[错误] 园区识别词表是空的')
    return {'parks': parks, 'buckets': buckets}


def validate(data):
    """园区文件夹必须真实存在，否则拒绝写盘。"""
    bad = []
    for folder in data['parks']:
        if os.path.isdir(MATERIALS_ROOT) and not os.path.isdir(os.path.join(MATERIALS_ROOT, folder)):
            bad.append(folder)
    if bad:
        raise SystemExit('[错误] 素材根目录 %s 下找不到这些园区文件夹：%s\n'
                         '       请改 Excel 或先建目录，本次未写入任何配置。'
                         % (MATERIALS_ROOT, '、'.join(bad)))
    # 通用例外里写的园区名也要真实存在，避免打错字静默失效
    known = set(data['parks'])
    for b, cfg in data['buckets'].items():
        for p in cfg.get('exclude', []):
            if p not in known:
                raise SystemExit('[错误] 桶 %s 的通用例外「%s」不在园区表里，请检查拼写。'
                                 '本次未写入任何配置。' % (b, p))
    # 邻近园区同样校验，打错字会让"邻近优先"静默失效
    for folder, info in data['parks'].items():
        for p in info.get('nearby', []):
            if p not in known:
                raise SystemExit('[错误] 园区 %s 的邻近园区「%s」不在园区表里，请检查拼写。'
                                 '本次未写入任何配置。' % (folder, p))
            if p == folder:
                raise SystemExit('[错误] 园区 %s 的邻近园区不能写自己。本次未写入任何配置。' % folder)


def main():
    argv = list(sys.argv[1:])
    kw_from_table = '--keywords-from-table' in argv
    argv = [a for a in argv if not a.startswith('--')]
    path = argv[0] if argv else DEFAULT_XLSX
    if not os.path.isfile(path):
        raise SystemExit('[错误] 找不到表格：%s' % path)
    data = read_xlsx(path)
    validate(data)
    old = park_rules.load()
    data['version'] = int(old.get('version', 1)) + 1
    import time
    data['updated'] = time.strftime('%Y-%m-%d %H:%M:%S')
    park_rules.save(data)

    if kw_from_table:
        import_keywords(data)
    else:
        print('  （D 列素材侧关键词未导入；要按表生效请加 --keywords-from-table）')

    print('已导入:', path)
    print('  园区数:', len(data['parks']))
    for f, info in data['parks'].items():
        near = info.get('nearby') or []
        print('    -', f, '显示名', info['display'], '| 识别词', '/'.join(info['aliases']),
              '| 优先级', info.get('priority', 1),
              ('| 邻近 ' + '/'.join(near)) if near else '| 邻近 （无）')
    cross = [b for b, c in data['buckets'].items() if c['cross']]
    print('  可跨园区通用的桶:', '、'.join(cross) or '（无）')
    for b in cross:
        exc = data['buckets'][b].get('exclude') or []
        if exc:
            print('    -', b, '排除：', '、'.join(exc))
    print('配置已写入:', park_rules.CONFIG_PATH)
    print('完整用法见表格「使用手册」页')


if __name__ == '__main__':
    main()
