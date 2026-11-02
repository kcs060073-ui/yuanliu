# -*- coding: utf-8 -*-
"""打包发布版 zip（说明书 + 表格 + 桌面快捷方式 + 技能本体）。

用法：
    python scripts/pack_release.py                    # 版本号取「现成发布清单」，没有才用今天 P01
    python scripts/pack_release.py --version 260916-P05

约定（P05 §8）
--------------
  - 输出到 桌面\\源流迭代\\源流park-video-skill-<版本>.zip
  - 外层放：00-部署说明.txt / 使用手册 docx / 语义规则分配表.xlsx /
    创建桌面快捷方式.bat / shortcut-help.txt / install_p05.py / 安装或升级P05.bat
  - 使用手册自动取桌面上最新的那份（内容以最新为准），包内改名为 <手册名>-<YYMMDD>.docx
  - 技能本体排除本机资产：output\\、state\\、__pycache__、_tmp、_tts_probe、
    gui_settings.json、*.bak
  - **版本号不硬编码**：缺省从 ``release_manifest.json`` 读（见 default_version()），
    并且写进 zip 的 00-部署说明.txt 会被同步成本次版本 —— 免得"包是 P05、
    说明书还写着 P03"这种只有同事才发现得了的错。
  - 写进 zip 的使用手册 / 表格会先做一次**隐私清洗**（见 sanitize_docx_bytes）：
    管理员机器的 ``C:\\Users\\<某人>...`` 路径不会流到同事手上。
"""
import datetime
import argparse
import glob
import io
import json
import os
import re
import sys
import zipfile

import release_manifest
import platform_paths as _pp

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK_ROOT = os.path.dirname(SKILL_DIR)
# 桌面目录走平台抽象：Windows 仍是 %USERPROFILE%\Desktop，macOS 是 ~/Desktop
DESK = _pp.desktop_dir()
OUTDIR = os.path.join(DESK, '源流迭代')
ROOT = '源流park-video-skill'

TABLE_IN_ZIP = '语义规则分配表.xlsx'
BAT_SRC = os.path.join(SKILL_DIR, 'workbench', '创建桌面快捷方式.bat')
BAT_IN_ZIP = '创建桌面快捷方式.bat'
HELP_SRC = os.path.join(SKILL_DIR, 'workbench', 'shortcut-help.txt')
HELP_IN_ZIP = 'shortcut-help.txt'
TABLE_SRC = os.path.join(PACK_ROOT, TABLE_IN_ZIP)
DEPLOY_IN_ZIP = '00-部署说明.txt'
DEPLOY_SRC = os.path.join(PACK_ROOT, DEPLOY_IN_ZIP)
INSTALL_PY = os.path.join(PACK_ROOT, 'install_p05.py')
INSTALL_BAT = os.path.join(PACK_ROOT, '安装或升级P05.bat')

EX_DIRS = {'__pycache__', '_tmp', '_tts_probe'}
EX_FILES = {'gui_settings.json'}
# 整目录排除（本机资产，各机器各留一份）
EX_TOP = {'output', 'state'}

# 部署说明里的版本串形态。
# 必须包含**安装器文件名**里的那个版本段：老正则只认 ``\bP\d{2}\b``，
# 而 ``install_p03.py`` 里是**小写 p**、且前面是下划线（`_p` 不构成 \b 边界），
# 于是同步永远漏掉它 —— 包都升到 P05 了，部署说明还写着 install_p03.py，
# 同事双击前会怀疑自己下错了包。这类"文件名里的版本"要单独认。
_VER_RE = re.compile(r'\b\d{6}-P\d{2}\b'        # 260916-P05
                     r'|\bP\d{2}\b'              # P05
                     r'|install_[pP]\d{2}\b'     # install_p05
                     r'|安装或升级[pP]\d{2}\b')   # 安装或升级P05


def sync_deploy_text(raw_bytes, ver):
    """把部署说明里的旧版本串统一成本次版本，再写进 zip。

    覆盖四种写法：``260916-P03`` / ``P03`` / ``install_p03.py`` / ``安装或升级P03.bat``。
    不动其它数字 —— 手工维护的说明里还有 3 步流程、3 毛租金之类的正文。
    """
    txt = raw_bytes.decode('utf-8', errors='replace')
    short = ver.split('-')[-1]                      # P05

    def _sub(m):
        s = m.group(0)
        low = s.lower()
        if low.startswith('install_'):
            # 安装器脚本名是 **全小写** 的 install_pNN.py（Windows 虽然不区分大小写，
            # 但说明文字里出现 install_P05.py 会让人怀疑自己看错了文件 ——
            # 保持与真实文件名逐字符一致）。
            return 'install_' + short.lower()
        if s.startswith('安装或升级'):
            return '安装或升级' + short
        if '-' in s:                                # 260916-P03 → 260916-P05
            return ver
        return short                                # P03 → P05

    return _VER_RE.sub(_sub, txt).encode('utf-8')


# 发布包里**不允许**出现的"指向旧版本的有效操作指令"。
#
# 只挡四类**会让人当成当前版本去操作**的写法：
#   1) 安装器文件名写成旧版（同事会去找一个已经没人发的 install_p03.py）
#   2) 诊断建议里点名旧版本（"使用 P03 完整安装包升级"）
#   3) 用户可见标题里带旧版本（"## P03 团队版运行原则"）
#   4) frontmatter 的 version 还是旧版本
# 历史叙述（"P04 及以前…"、"从 P02 可升级"）、release_manifest 的
# ``min_upgrade_from`` 属于正常记录，**不挡** —— 用户明确说这类可以保留。
_STALE_PATTERNS = (
    (re.compile(r'install_[pP]0[1-4]\b'), '安装脚本文件名指向旧版'),
    (re.compile(r'安装或升级[pP]0[1-4]\b'), '安装批处理名指向旧版'),
    (re.compile(r'[pP]0[1-4]\s*完整安装包'), '诊断建议写死旧版本'),
    (re.compile(r'^#{1,6}[^\n]*\bP0[1-4]\b', re.M), '用户可见标题写着旧版本'),
    (re.compile(r'^\s*version:\s*\d{6}-P0[1-4]\b', re.M), 'frontmatter 版本号是旧版'),
)
# 同一行里出现这些词 → 判定为"在讲历史来源版本"，放行
_STALE_HISTORY_OK = ('历史', '从P', '从 P', '来源版本', 'min_upgrade',
                     '及以前', '以前版本', '旧版')

# 扫描器与它的测试**自身必然包含**这些模式（正则字面量、反例字符串），
# 属于构造性命中，不是"用户可见文案"。豁免它们，否则会把检查器自己判成违规。
# 这类豁免只放行这两个文件 —— 其它任何文件命中都视为真问题。
STALE_SCAN_EXEMPT = (
    'scripts/pack_release.py',              # 本扫描器
    'tests/test_release_wording.py',        # 扫描器的测试（含反例）
)


def is_stale_scan_exempt(label):
    lab = (label or '').replace('\\', '/')
    return any(lab.endswith(x) for x in STALE_SCAN_EXEMPT)


def find_stale_wording(text, label):
    """返回 text 里"会让人以为当前版本是旧版"的命中（已排除历史来源叙述）。

    扫描器自己与它的测试会被豁免（见 ``STALE_SCAN_EXEMPT``）。
    """
    if is_stale_scan_exempt(label):
        return []
    hits = []
    for rx, why in _STALE_PATTERNS:
        for m in rx.finditer(text or ''):
            line = text[text.rfind('\n', 0, m.start()) + 1:
                        (text.find('\n', m.end()) if text.find('\n', m.end()) >= 0
                         else len(text))]
            if any(k in line for k in _STALE_HISTORY_OK):
                continue
            snippet = line.strip()[:70]
            hits.append('%s: %s ← %s' % (label, snippet, why))
    return hits


def default_version():
    """版本号来源：现成发布清单 > 今天 + P01。

    P05 §8「版本读发布清单不硬编码」：人工传 --version 当然可以，
    但忘了传时不应该悄悄生成一个和清单不一致的版本号。
    """
    try:
        with io.open(os.path.join(SKILL_DIR, 'release_manifest.json'),
                     encoding='utf-8') as f:
            v = (json.load(f) or {}).get('release_version', '')
        if v:
            return v
    except Exception:
        pass
    return datetime.date.today().strftime('%y%m%d') + '-P01'


_PRIVACY_SUBS = (
    (re.compile(r'[A-Za-z]:[\\/]Users[\\/](?![<%])[^\\/"\'<>%]+'), r'C:\\Users\\<你的用户名>'),
)

# 打包前对**技能本体**做硬性隐私扫描。为什么不在 zip 里顺手改写：
# 技能本体的每个文件都被 release_manifest.json 记了 sha256，upgrade.py
# 解包后会逐个核对；打包时改写字节 = 哈希对不上 = 同事升级直接失败。
# 所以本体的清理必须在**源码/文件层**完成，这里只做"不接受脏包"的把关。
#
# 扫描口径分两级（与 tests/test_template_privacy.py 一致）：
#   * 管理员用户名（拼接成 _PRIVATE_HARD）：任何文件里都不允许。
#   * 设备标识字段名：只查**数据文件**。``.py`` 里出现这些词是清洗逻辑本身
#     （sanitize_platform 的白名单），不是泄漏。
_PRIVATE_HARD = (''.join(('Tani', 'kawa')),)   # 片段拼接，理由见 tests/test_template_privacy.py
_HARD_BYTES = _PRIVATE_HARD[0].encode('ascii')
_PRIVATE_DATA_KW = ('device_id', 'hard_disk_id', 'mac_address')
_PRIVATE_TEXT_EXT = ('.py', '.md', '.txt', '.json', '.bat', '.csv', '.ini', '.cfg')


def private_hits(root):
    """扫描技能本体里会进包的文件，返回敏感命中列表。"""
    hits = []
    for cur, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in EX_DIRS]
        for fn in files:
            fp = os.path.join(cur, fn)
            rel = os.path.relpath(fp, root).replace('\\', '/')
            low = fn.lower()
            if excluded(rel) or not low.endswith(_PRIVATE_TEXT_EXT):
                continue
            txt = io.open(fp, 'rb').read().decode('utf-8', 'ignore')
            kws = _PRIVATE_HARD + (() if low.endswith('.py') else _PRIVATE_DATA_KW)
            for kw in kws:
                if kw in txt:
                    hits.append('%s[%s]' % (rel, kw))
                    break
    return hits


def sanitize_bytes(data, label, hits=None):
    """通用隐私清洗：把管理员机器上的具体用户目录换成占位符。

    P05 §6 要求"发布包全文不得出现管理员用户名"。技能本体里的文本文件已经
    在源码层清理干净；但**使用手册 docx / 规则表 xlsx 是二进制容器**
    （zip），里面的 document.xml 还是可能带路径 —— 而且手册那份可能是
    从桌面取的、并不在我们控制的文件里。所以写进 zip 前统一洗一遍。
    """
    if isinstance(data, str):
        txt = data
        for rx, rep in _PRIVACY_SUBS:
            if rx.search(txt):
                if hits is not None:
                    hits.append(label)
                txt = rx.sub(rep, txt)
        return txt.encode('utf-8')
    return data


def sanitize_zip_member(data, label, hits=None):
    """对 docx/xlsx 这类 zip 容器：只洗内部 xml，其它成员原样保留。"""
    try:
        src = zipfile.ZipFile(io.BytesIO(data))
    except Exception:
        return data
    buf = io.BytesIO()
    changed = False
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            payload = src.read(item.filename)
            if item.filename.endswith(('.xml', '.rels')) and _HARD_BYTES in payload:
                new = sanitize_bytes(payload.decode('utf-8', 'replace'), label, hits)
                if new != payload:
                    changed = True
                payload = new
            dst.writestr(item, payload)
    src.close()
    return buf.getvalue() if changed else data


def sanitize_packaged(fp, label, hits=None):
    """按文件类型选择清洗方式，返回可直接写进 zip 的 bytes。"""
    low = fp.lower()
    data = io.open(fp, 'rb').read()
    if low.endswith(('.docx', '.xlsx', '.pptx')):
        return sanitize_zip_member(data, label, hits)
    if low.endswith(('.txt', '.md', '.json', '.py', '.bat', '.csv')):
        return sanitize_bytes(data.decode('utf-8', 'replace'), label, hits)
    return data


def newest_manual():
    """桌面上最新的《源流园区短视频素材分配规则使用手册*.docx》"""
    cands = (glob.glob(os.path.join(PACK_ROOT, '源流园区短视频素材分配规则使用手册*.docx'))
             + glob.glob(os.path.join(DESK, '源流园区短视频素材分配规则使用手册*.docx')))
    cands = [c for c in cands if not os.path.basename(c).startswith('~$')]
    if not cands:
        raise SystemExit('桌面上找不到《源流园区短视频素材分配规则使用手册*.docx》')
    return max(cands, key=os.path.getmtime)


def excluded(rel):
    parts = rel.replace('\\', '/').split('/')
    if any(p in EX_DIRS for p in parts):
        return True
    name = parts[-1]
    if name in EX_FILES or name.endswith(('.pyc', '.pyo')) or '.bak' in name:
        return True
    if parts[0] in EX_TOP:            # output/ state/：本机资产
        return True
    return False


def human_date(ver6):
    """260916 -> 2026-09-16"""
    return ('20%s-%s-%s' % (ver6[0:2], ver6[2:4], ver6[4:6])) if len(ver6) == 6 else ver6


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--version', default='',
                    help='缺省读 release_manifest.json 里的 release_version')
    ap.add_argument('--out', default='', help='输出 zip；缺省为桌面\\源流迭代')
    args = ap.parse_args()
    today = datetime.date.today().strftime('%y%m%d')
    ver = (args.version or default_version()).strip()      # §8：不硬编码版本
    ver6 = ver.split('-')[0] if re.match(r'^\d{6}', ver) else today   # 260916
    ver_date = human_date(ver6)

    manual_src = newest_manual()
    manual_name = re.sub(r'-\d{6}\.docx$', '', os.path.basename(manual_src)) \
        + '-%s.docx' % ver6

    for p in (manual_src, TABLE_SRC, BAT_SRC, HELP_SRC, DEPLOY_SRC, INSTALL_PY, INSTALL_BAT):
        if not os.path.isfile(p):
            raise SystemExit('缺少文件：' + p)

    # 把关一：技能本体必须已经是干净的（源码层清理完成）
    hits = private_hits(SKILL_DIR)
    if hits:
        raise SystemExit('拒绝打包：技能本体仍含敏感串，请先在源码层清理：\n  '
                         + '\n  '.join(hits[:20]))

    release_manifest.build(SKILL_DIR, ver)
    out = os.path.abspath(args.out) if args.out else os.path.join(
        OUTDIR, '源流park-video-skill-%s.zip' % ver)
    os.makedirs(os.path.dirname(out), exist_ok=True)

    scrubbed = []
    n = 0
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        # 外层 7 项。文本/容器类先做隐私清洗；部署说明额外把版本串同步成本次版本。
        # 这些文件不在 release_manifest 的哈希清单里，改写字节是安全的。
        z.writestr(DEPLOY_IN_ZIP,
                   sync_deploy_text(io.open(DEPLOY_SRC, 'rb').read(), ver))
        z.writestr('install_p05.py',
                   sanitize_packaged(INSTALL_PY, 'install_p05.py', scrubbed))
        z.writestr('安装或升级P05.bat', io.open(INSTALL_BAT, 'rb').read())
        z.writestr(manual_name,
                   sanitize_packaged(manual_src, manual_name, scrubbed))
        z.writestr(TABLE_IN_ZIP,
                   sanitize_packaged(TABLE_SRC, TABLE_IN_ZIP, scrubbed))
        z.writestr(BAT_IN_ZIP,
                   sanitize_packaged(BAT_SRC, BAT_IN_ZIP, scrubbed))
        z.writestr(HELP_IN_ZIP,
                   sanitize_packaged(HELP_SRC, HELP_IN_ZIP, scrubbed))
        # 技能本体：**原样**写入。它们受 release_manifest 的 sha256 保护，
        # 任何字节改写都会让同事升级时的哈希校验失败（把关一已保证内容干净）。
        for cur, dirs, files in os.walk(SKILL_DIR):
            dirs[:] = [d for d in dirs if d not in EX_DIRS]
            for fn in files:
                fp = os.path.join(cur, fn)
                rel = os.path.relpath(fp, SKILL_DIR)
                if excluded(rel):
                    continue
                z.write(fp, ROOT + '/' + rel.replace('\\', '/'))
                n += 1

    # 把关二：把打包结果再验一遍（模拟同事解包后看到的内容）
    with zipfile.ZipFile(out) as z:
        bad = []
        for item in z.namelist():
            if item.endswith('/'):
                continue
            data = z.read(item)
            if item.lower().endswith(('.docx', '.xlsx', '.pptx')):
                try:
                    inner = zipfile.ZipFile(io.BytesIO(data))
                    data = b'\n'.join(inner.read(n) for n in inner.namelist()
                                      if n.endswith(('.xml', '.rels')))
                except Exception:
                    continue
            if b'Tani' + b'kawa' in data:
                bad.append(item)
    if bad:
        raise SystemExit('拒绝交付：打包结果里仍有管理员用户名：%s' % '、'.join(bad[:10]))

    # 把关三：用户可见文案里不得出现**指向旧版本的有效操作指令**。
    # 这类问题只有拿到包的人会踩（照着说明去找 install_p03.py、或以为装错了版本），
    # 打包机上一切正常 —— 所以必须在打包时挡住，不能只靠自觉。
    stale = []
    with zipfile.ZipFile(out) as z:
        for item in z.namelist():
            if item.endswith('/'):
                continue
            low = item.lower()
            if low.endswith(('.docx', '.xlsx', '.pptx', '.ico', '.jpg', '.png')):
                continue
            try:
                txt = z.read(item).decode('utf-8', 'ignore')
            except Exception:
                continue
            stale += find_stale_wording(txt, item)
    if stale:
        raise SystemExit('拒绝交付：包内仍有指向旧版本的用户可见文案：\n  '
                         + '\n  '.join(stale[:10])
                         + '\n  修好源文件（或让 pack_release 的版本同步覆盖它）后重打。')

    print('打包完成：', out)
    print('  版本        ：%s（%s）' % (ver, ver_date))
    print('  手册来源    ：%s -> %s' % (os.path.basename(manual_src), manual_name))
    print('  技能内文件  ：%d 个 + 外层 7 项' % n)
    if scrubbed:
        print('  隐私清洗    ：%d 处（%s）' % (len(scrubbed), '、'.join(scrubbed[:5])))
    else:
        print('  隐私清洗    ：无需清洗')
    print('  大小        ：%.2f MB' % (os.path.getsize(out) / 1024 / 1024))
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
