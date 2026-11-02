# -*- coding: utf-8 -*-
"""产出台账（P0-③）：每生成一条草稿写一行 CSV，批量结束可秒定位问题条目。

它解决什么
----------
1 个人用 skill，出错了自己知道；15 个人批量出片，出问题时的第一个问题永远是
「**谁的哪一条**」。没有台账就只能挨个问、挨个打开草稿看。
有了台账，一句命令就能列出今天所有 FAIL：
    python scripts/production_log.py --summary --fail-only

另外一个隐性价值：素材库版本。同一个脚本，上周出片正常、这周画面不对，
第一嫌疑就是素材库被换过（新增/删了素材）。台账记下每次出片时的素材库指纹，
一对比就知道是不是素材变了。

文件位置
--------
默认 <skill>/output/production_log.csv，可用 --log 指定。
CSV 用 UTF-8 **带 BOM**，双击用 Excel 打开中文不乱码。

用法
----
    python scripts/production_log.py --summary              # 全部汇总
    python scripts/production_log.py --summary --fail-only   # 只看问题条目
    python scripts/production_log.py --summary --last 20     # 最近 20 条
    python scripts/production_log.py --merge D:\\汇总目录     # 合并 15 人的台账
    python scripts/production_log.py --version-of E:\\园区素材  # 查素材库指纹
"""
import argparse
import csv
import glob
import hashlib
import os
import re
import socket
import sys
import tempfile
from collections import Counter, OrderedDict

from atomic_io import FileLock

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
DEFAULT_LOG = os.path.join(SKILL_DIR, 'output', 'production_log.csv')


def default_log_path():
    if (os.environ.get('YUANLIU_RUN_MODE') or '').lower() == 'test':
        root = os.environ.get('YUANLIU_TEST_ROOT') or os.path.join(tempfile.gettempdir(), 'yuanliu-tests')
        return os.path.join(root, 'state', 'production_log.csv')
    return DEFAULT_LOG

# 2026-09-15：图片也参与选片，指纹要跟着变
VIDEO_EXTS = ('.mp4', '.mov', '.avi', '.mkv',
              '.jpg', '.jpeg', '.png', '.webp', '.bmp', '.jfif')

FIELDS = [
    'timestamp',     # 出片时间
    'editor',        # 剪辑者（7式 --editor；缺省取系统用户名）
    'host',          # 机器名（15 人合并后区分来源）
    'park',          # 园区
    'style',         # 风格
    'draft_name',    # 草稿名
    'duration_s',    # 成片时长（秒）
    'materials_version',   # 素材库指纹（变了说明素材被换过）
    'materials_dir',       # 素材库路径
    'orientation',   # 横/竖
    'subtitles',     # 字幕条数
    'videos',        # 用到的素材数
    'bgm',           # BGM 名
    'verify',        # 自检结果 PASS / FAIL:原因 / SKIP
    'skill_version',
]


# ---------------------------------------------------------------------------
# 素材库版本（指纹）
# ---------------------------------------------------------------------------
def materials_version(materials_dir):
    """素材库指纹：v<文件数>-<hash8>。

    只用「相对路径 + 文件大小」算哈希，不用 mtime ——
    mtime 会随复制/备份变化，同一批素材换个盘就成了两个版本，失去对比意义。
    代价：内容被替换但大小不变时检测不到，对当前用途够用。
    """
    if not materials_dir or not os.path.isdir(materials_dir):
        return '-'
    entries = []
    for root, _, fnames in os.walk(materials_dir):
        for fn in fnames:
            if fn.lower().endswith(VIDEO_EXTS):
                fp = os.path.join(root, fn)
                try:
                    sz = os.path.getsize(fp)
                except OSError:
                    continue
                rel = os.path.relpath(fp, materials_dir)
                entries.append('%s|%d' % (rel.replace('\\', '/'), sz))
    if not entries:
        return 'v0-empty'
    entries.sort()
    h = hashlib.sha1('\n'.join(entries).encode('utf-8')).hexdigest()[:8]
    return 'v%d-%s' % (len(entries), h)


def _code_fingerprint():
    """skill 自身代码指纹：scripts/*.py + config/*.json 内容哈希前 8 位。"""
    h = hashlib.sha1()
    files = []
    for sub in ('scripts', 'config'):
        d = os.path.join(SKILL_DIR, sub)
        if os.path.isdir(d):
            files += sorted(glob.glob(os.path.join(d, '*.py'))
                            + glob.glob(os.path.join(d, '*.json')))
    for fp in files:
        h.update(os.path.basename(fp).encode('utf-8'))
        try:
            with open(fp, 'rb') as f:
                h.update(f.read())
        except OSError:
            pass
    return h.hexdigest()[:8]


def skill_version():
    """skill 自身版本：优先 SKILL.md 的 version 字段，没有则用代码指纹 s-xxxx。

    为什么回退到指纹：人工维护的版本号最容易忘改，而「谁还没升级」恰恰是
    台账最该发现的问题之一。文件内容一变指纹就变，不依赖人记着改。
    """
    p = os.path.join(SKILL_DIR, 'SKILL.md')
    try:
        with open(p, encoding='utf-8') as f:
            head = f.read(2000)
        m = re.search(r'(?mi)^\s*version:\s*(\S+)', head)
        if m:
            return m.group(1)
    except OSError:
        pass
    return 's-' + _code_fingerprint()


def default_editor():
    return (os.environ.get('PARK_EDITOR')
            or os.environ.get('USERNAME')
            or os.environ.get('USER')
            or '-')


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------
def append_record(log_path=None, **kw):
    """追加一行台账。**任何异常都不许把出片流程搞挂** —— 台账是辅助。"""
    path = log_path or default_log_path()
    row = OrderedDict((f, kw.get(f, '')) for f in FIELDS)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with FileLock(path, timeout=30):
            new = not os.path.isfile(path) or os.path.getsize(path) == 0
            with open(path, 'a', encoding='utf-8-sig', newline='') as f:
                w = csv.DictWriter(f, fieldnames=FIELDS)
                if new:
                    w.writeheader()
                w.writerow(row)
                f.flush()
                os.fsync(f.fileno())
        return path
    except Exception as e:
        print('[台账] 写入失败（不影响出片）：%s' % e)
        return None


def read_log(log_path=None):
    path = log_path or default_log_path()
    if not os.path.isfile(path):
        return []
    with open(path, encoding='utf-8-sig', newline='') as f:
        return [r for r in csv.DictReader(f)]


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
def summarize(rows, fail_only=False, last=None):
    out = []
    rows = list(rows)
    if last:
        rows = rows[-last:]
    bad = [r for r in rows if not (r.get('verify') or '').startswith('PASS')]
    if fail_only:
        rows = bad

    out.append('产出台账汇总：共 %d 条' % len(rows))
    if not rows:
        out.append('  （无记录）')
        return '\n'.join(out)

    ver = Counter((r.get('verify') or '-').split(':')[0] for r in rows)
    out.append('  自检结果：%s' % ' / '.join('%s %d' % (k, v) for k, v in ver.most_common()))

    for field, title in (('editor', '按剪辑者'), ('park', '按园区'), ('style', '按风格')):
        c = Counter((r.get(field) or '-') for r in rows)
        out.append('  %s：%s' % (title, '、'.join('%s(%d)' % (k, v) for k, v in c.most_common())))

    mver = Counter((r.get('materials_version') or '-') for r in rows)
    if len(mver) > 1:
        out.append('  ⚠ 素材库版本不止一个：%s —— 同一批脚本画面不一致先查这个'
                   % '、'.join('%s(%d)' % (k, v) for k, v in mver.most_common()))

    if bad:
        out.append('')
        out.append('  问题条目 %d 条：' % len(bad))
        for r in bad:
            out.append('    ✗ [%s] %s | %s | %s | %s'
                       % (r.get('timestamp', '')[:16], r.get('editor', '-'),
                          r.get('draft_name', '-'), r.get('park', '-'),
                          r.get('verify', '-')))
    return '\n'.join(out)


def merge_logs(src_dir, dst):
    """合并多份台账（15 人各交一份到共享盘后汇总）。按 timestamp 排序去重。"""
    seen = set()
    rows = []
    for p in glob.glob(os.path.join(src_dir, '**', '*production_log*.csv'), recursive=True):
        for r in read_log(p):
            key = (r.get('timestamp'), r.get('draft_name'), r.get('host'))
            if key in seen:
                continue
            seen.add(key)
            rows.append(r)
    rows.sort(key=lambda r: r.get('timestamp') or '')
    os.makedirs(os.path.dirname(dst) or '.', exist_ok=True)
    with open(dst, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(OrderedDict((k, r.get(k, '')) for k in FIELDS))
    return dst, len(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--log', default=default_log_path(), help='台账 CSV 路径')
    ap.add_argument('--summary', action='store_true', help='打印汇总')
    ap.add_argument('--fail-only', action='store_true', help='只看问题条目')
    ap.add_argument('--last', type=int, help='只看最近 N 条')
    ap.add_argument('--merge', metavar='DIR', help='合并目录下所有台账到 --log')
    ap.add_argument('--version-of', metavar='DIR', help='打印素材库指纹')
    args = ap.parse_args()

    if args.version_of:
        print(materials_version(args.version_of))
        return 0
    if args.merge:
        dst, n = merge_logs(args.merge, args.log)
        print('已合并 %d 条 → %s' % (n, dst))
        print(summarize(read_log(dst), args.fail_only, args.last))
        return 0
    print(summarize(read_log(args.log), args.fail_only, args.last))
    print('\n台账文件：%s' % args.log)
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
