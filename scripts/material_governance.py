# -*- coding: utf-8 -*-
"""素材治理（P1-①）：使用频次记录 + 大库降权 + 素材库基准版本校验。

三件事，各解决一个真实问题
--------------------------
1. **使用频次记录**：15 人批量出片，如果没有记录，谁也不知道哪些素材被用滥了。
   `output/material_usage.json` 记每个素材被选中的次数与最后使用时间。

2. **大库降权**：素材用滥了，成片看着都一个样。高频素材降低被选中概率，
   让池子里没露过面的素材先上。

   ⚠️ **只对大库启用（默认 ≥500 个素材）** —— 这是 2026-09-08 贵阳实测得出的硬约束：
   小库（贵阳仅 41 条素材）本来就没得选，再降权等于把可选范围压到个位数，
   成片反而更单调甚至凑不出素材。当前只有新疆园区（547）够格。

3. **基准版本校验**：素材库每人本地一份，没有中心可比对。做法是由负责人
   导出一份基准指纹（`--set-baseline`），其他人本地 `--check` 比对 ——
   不一致说明素材被换过/拷漏了，在出片前就知道，而不是等画面不对才发现。

用法
----
    python scripts/material_governance.py --report E:\\园区素材\\嘉兴园区
    python scripts/material_governance.py --top 20            # 用得最多的素材
    python scripts/material_governance.py --set-baseline E:\\园区素材\\嘉兴园区
    python scripts/material_governance.py --check E:\\园区素材\\嘉兴园区
    python scripts/material_governance.py --reset-usage       # 清空频次（换素材库后）
"""
import argparse
import json
import os
import sys
import time

from atomic_io import locked_json_update
from runtime_context import usage_path as runtime_usage_path

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
OUTPUT_DIR = os.path.join(SKILL_DIR, 'output')
USAGE_PATH = os.path.join(OUTPUT_DIR, 'material_usage.json')
BASELINE_PATH = os.path.join(SKILL_DIR, 'config', 'materials_baseline.json')

# 降权门槛：**按池（bucket）判断，不是按整个库**。
# 这是 2026-09-08 贵阳实测 + 2026-09-09 落地验证共同得出的结论：
#   - 按库判断太粗：嘉兴 123 条素材，但竖屏过滤后每个语义池只有十几个，
#     若按库判断（≥500）则永远不降权，等于功能失效；
#   - 反过来，按库判断若阈值调低，又会给"某池只有 2~3 个素材"的库降权，
#     直接把可选范围压没 —— 这才是 09-08 担心的"小库降权有害"。
# 真正该衡量的是**当前这个池有多少可选素材**：池够大才降权。
# 30 是经验值：池内 30+ 个素材时，反复撞车才成为可感知的问题。
DOWNWEIGHT_MIN_POOL = 30
# 兼容旧引用（工作台/脚本可能读过这个名字）
DOWNWEIGHT_MIN_LIB = DOWNWEIGHT_MIN_POOL
# 降权时向前看多少个候选（太少则降权没效果，太多则破坏轮转去重）
DOWNWEIGHT_WINDOW = 8
# 超过这个使用次数，降权最重（权重不再继续下降）
USAGE_SATURATION = 10


# ---------------------------------------------------------------------------
# 频次记录
# ---------------------------------------------------------------------------
def load_usage(path=None):
    path = path or runtime_usage_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        return data.get('items', {}) if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_usage(usage, path=None):
    path = path or runtime_usage_path()
    try:
        def replace(_old):
            return {'updated': time.strftime('%Y-%m-%d %H:%M:%S'), 'items': usage}
        locked_json_update(path, replace, default={'items': {}})
        return True
    except Exception as e:
        print('[素材治理] 频次写入失败（不影响出片）：%s' % e)
        return False


def record_usage(paths, usage=None):
    """记录一批素材被使用。传入素材绝对路径列表。"""
    usage = usage if usage is not None else load_usage()
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    for p in paths or []:
        if not p:
            continue
        key = os.path.normpath(os.path.abspath(p)).replace('\\', '/')
        rec = usage.get(key) or {'n': 0, 'last': ''}
        rec['n'] = int(rec.get('n', 0)) + 1
        rec['last'] = now
        usage[key] = rec
    return usage


def record_usage_atomic(paths, path=None):
    """跨进程安全地累计使用次数，避免并发出片丢计数。"""
    path = path or runtime_usage_path()
    normalized = [_key(p) for p in (paths or []) if p]
    now = time.strftime('%Y-%m-%d %H:%M:%S')

    def update(data):
        if not isinstance(data, dict):
            data = {}
        items = data.get('items') if isinstance(data.get('items'), dict) else {}
        for key in normalized:
            rec = items.get(key) or {'n': 0, 'last': ''}
            rec['n'] = int(rec.get('n', 0)) + 1
            rec['last'] = now
            items[key] = rec
        return {'updated': now, 'items': items}

    data = locked_json_update(path, update, default={'items': {}})
    return data.get('items', {})


# ---------------------------------------------------------------------------
# 降权
# ---------------------------------------------------------------------------
def should_downweight(pool_size, threshold=DOWNWEIGHT_MIN_POOL):
    """是否对**这个池**启用降权。

    传入的是当前语义池的素材数（不是整个素材库）。池太小返回 False ——
    宁可不降权，也不能把可选范围压没。
    """
    return pool_size >= threshold


def usage_weight(n):
    """使用 n 次后的选中权重（越大越优先）。

    n=0 → 1.0（全新素材最优先）；n=10 及以上 → 0.1（降到底但不会为 0，
    保证极端情况下仍有素材可用）。
    """
    n = max(0, int(n or 0))
    if n >= USAGE_SATURATION:
        return 0.1
    return 1.0 - 0.09 * n


def pick_least_used(cands, usage, window=DOWNWEIGHT_WINDOW, return_pos=False):
    """从候选里挑「用得最少」的，保留轮转顺序打底。

    cands 已按轮转游标排好序（保证不邻近重复）。这里只看前 window 个，
    在其中按 usage 升序挑；usage 相同时取靠前的（保持原轮转行为）。
    只看窗口而不是全池，是为了不让降权把轮转去重完全覆盖掉。
    """
    if not cands:
        return (0, None) if return_pos else None
    head = cands[:window]
    best, best_n, best_i = None, None, 0
    for i, it in enumerate(head):
        key = _key(it)
        n = int((usage.get(key) or {}).get('n', 0))
        if best_n is None or n < best_n:
            best, best_n, best_i = it, n, i
    return (best_i, best) if return_pos else best


def _key(item):
    p = item.get('path') if isinstance(item, dict) else item
    return os.path.normpath(os.path.abspath(p)).replace('\\', '/') if p else ''


# ---------------------------------------------------------------------------
# 基准版本校验
# ---------------------------------------------------------------------------
def fingerprint(materials_dir):
    """素材库指纹（与 production_log.materials_version 同一算法）。"""
    try:
        sys.path.insert(0, HERE)
        from production_log import materials_version
        return materials_version(materials_dir)
    except Exception:
        return '-'


def set_baseline(materials_dir, note=''):
    """把当前素材库指纹设为团队基准（负责人执行）。"""
    data = {}
    if os.path.isfile(BASELINE_PATH):
        try:
            with open(BASELINE_PATH, encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            data = {}
    name = os.path.basename((materials_dir or '').rstrip('/\\'))
    data[name] = {
        'version': fingerprint(materials_dir),
        'path': materials_dir,
        'set_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'by': os.environ.get('USERNAME') or os.environ.get('USER') or '',
        'note': note,
    }
    os.makedirs(os.path.dirname(BASELINE_PATH), exist_ok=True)
    with open(BASELINE_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data[name]


def check_baseline(materials_dir):
    """比对本地素材库与团队基准。返回 dict。"""
    name = os.path.basename((materials_dir or '').rstrip('/\\'))
    if not os.path.isfile(BASELINE_PATH):
        return {'status': 'NO_BASELINE', 'name': name,
                'local': fingerprint(materials_dir)}
    try:
        with open(BASELINE_PATH, encoding='utf-8') as f:
            base = json.load(f)
    except Exception:
        return {'status': 'NO_BASELINE', 'name': name,
                'local': fingerprint(materials_dir)}
    b = base.get(name)
    if not b:
        return {'status': 'NOT_IN_BASELINE', 'name': name,
                'local': fingerprint(materials_dir)}
    local = fingerprint(materials_dir)
    return {'status': 'OK' if local == b.get('version') else 'DRIFT',
            'name': name, 'local': local, 'baseline': b.get('version'),
            'set_at': b.get('set_at', ''), 'by': b.get('by', '')}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def report(materials_dir, usage, top=10):
    """素材库体检 + 使用分布。"""
    from collections import Counter
    try:
        sys.path.insert(0, HERE)
        import material_matcher as mm
        items = mm.scan_materials(materials_dir, with_duration=False)
        by_bucket = Counter(it['bucket'] or 'None' for it in items)
    except Exception:
        items, by_bucket = [], Counter()
    n = len(items)
    fp = fingerprint(materials_dir)
    lines = ['素材库：%s' % materials_dir,
             '  素材数   %d' % n,
             '  指纹     %s' % fp,
             '  降权     按池判断（池内 >= %d 个素材才降权，见下方「降权池」）'
             % DOWNWEIGHT_MIN_POOL]
    if by_bucket:
        lines.append('  分桶     %s' % '、'.join('%s(%d)' % (k, v)
                                                 for k, v in by_bucket.most_common()))
        dw = [(k, v) for k, v in by_bucket.most_common() if should_downweight(v)]
        if dw:
            lines.append('  降权池   %s（池内 >= %d 个才降权）'
                         % ('、'.join('%s(%d)' % p for p in dw), DOWNWEIGHT_MIN_POOL))
        else:
            lines.append('  降权池   无（最大池 %d < %d，池太小降权会把可选范围压没）'
                         % (max(by_bucket.values()), DOWNWEIGHT_MIN_POOL))
    used = [(k, v['n'], v.get('last', '')) for k, v in usage.items()
            if (materials_dir or '').replace('\\', '/').lower() in k.lower()]
    used.sort(key=lambda x: -x[1])
    if used:
        lines.append('  已用素材 %d 个（累计 %d 次）' % (len(used), sum(u[1] for u in used)))
        never = n - len(used)
        if n:
            lines.append('  未用过   %d 个（%.0f%%）' % (never, 100.0 * never / n))
        for k, cnt, last in used[:top]:
            lines.append('    %3d 次  %s  %s' % (cnt, last[:10], os.path.basename(k)))
    else:
        lines.append('  已用素材 0 个（还没有出片记录）')
    ck = check_baseline(materials_dir)
    if ck['status'] == 'OK':
        lines.append('  基准比对 ✅ 与团队基准一致（%s 由 %s 设定）'
                     % (ck.get('set_at', '')[:10], ck.get('by', '-')))
    elif ck['status'] == 'DRIFT':
        lines.append('  基准比对 ⚠ 与基准不一致：本地 %s / 基准 %s（素材被换过或拷漏）'
                     % (ck['local'], ck['baseline']))
    else:
        lines.append('  基准比对 — 未设基准（负责人跑 --set-baseline）')
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('dir', nargs='?', help='素材库目录')
    ap.add_argument('--report', action='store_true', help='素材库体检 + 使用分布')
    ap.add_argument('--top', type=int, default=10, help='列出用得最多的 N 个素材')
    ap.add_argument('--set-baseline', metavar='DIR', help='把该素材库设为团队基准')
    ap.add_argument('--check', metavar='DIR', help='比对本地素材库与团队基准')
    ap.add_argument('--reset-usage', action='store_true', help='清空使用频次记录')
    args = ap.parse_args()

    if args.set_baseline:
        r = set_baseline(args.set_baseline)
        print('已设为团队基准：%s  %s' % (r['path'], r['version']))
        return 0
    if args.check:
        ck = check_baseline(args.check)
        tag = {'OK': '✅ 一致', 'DRIFT': '⚠ 不一致',
               'NO_BASELINE': '— 未设基准', 'NOT_IN_BASELINE': '— 该园区不在基准里'}[ck['status']]
        print('%s：%s' % (ck['name'], tag))
        print('  本地 %s' % ck['local'])
        if ck['status'] == 'DRIFT':
            print('  基准 %s（%s 由 %s 设定）' % (ck['baseline'], ck.get('set_at', '')[:10],
                                                  ck.get('by', '-')))
            print('  → 素材被换过或拷贝不完整，确认后重新拷贝，或让负责人重设基准')
        return 0 if ck['status'] in ('OK',) else 1
    if args.reset_usage:
        usage_file = runtime_usage_path()
        if os.path.isfile(usage_file):
            os.remove(usage_file)
            print('已清空使用频次：%s' % usage_file)
        else:
            print('无频次记录')
        return 0
    if args.dir:
        print(report(args.dir, load_usage(), args.top))
        return 0
    # 无参数：全局 TOP 榜
    usage = load_usage()
    items = sorted(usage.items(), key=lambda kv: -int(kv[1].get('n', 0)))
    print('素材使用总榜（共 %d 个素材被用过）' % len(items))
    for k, v in items[:args.top]:
        print('  %3d 次  %s  %s' % (int(v.get('n', 0)), v.get('last', '')[:10], k))
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
