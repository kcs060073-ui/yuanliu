# -*- coding: utf-8 -*-
"""横竖屏「整桶为空回退」回归（方案 B，2026-09-15 用户拍板）。

规则：
  - 某语义桶在本园区**同方向素材为 0** 时，允许回退用另一方向的素材
    （剪映按画布裁切，效果由用户在剪映判断）；
  - 同方向**有**素材的桶，行为完全不变（仍只用同方向）。

用法：
    python tests/test_orient_fallback.py
    pytest tests/test_orient_fallback.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
SCRIPTS_DIR = os.path.join(SKILL_DIR, 'scripts')
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import material_matcher as mm                          # noqa: E402

mm._pr = None                                          # 关掉园区锁定

SUBS = ['嘉兴园区招商', '高标准厂房', '全新厂房', '万方厂房']
DURS = [2_000_000, 3_000_000, 3_000_000, 3_000_000]


def _item(path, bucket, orientation, dur=5_000_000):
    return {'path': path, 'name': os.path.basename(path), 'bucket': bucket,
            'kind': 'video', 'duration': dur, 'orientation': orientation,
            'dir_chain': []}


def _mk(texts):
    return [{'text': t} for t in texts]


def _run(items, orientation, seed=7):
    mm.scan_materials = lambda *a, **k: list(items)
    m = mm.match_materials(_mk(SUBS), 'FAKE_DIR', sub_durations=DURS,
                           park=None, seed=seed, video_orientation=orientation)
    return mm.split_semantic_blocks(_mk(SUBS)), m


def _block_first_paths(blocks, m):
    return [m[b['idxs'][0]]['path'] if m[b['idxs'][0]] else None for b in blocks]


def test_fallback_when_no_same_orientation():
    """factory 只有横屏 → 竖版成片应回退用横屏（而不是留空/沿用上一段）。"""
    items = [_item('f%d.mp4' % i, 'factory', 'landscape') for i in range(3)]
    blocks, m = _run(items, 'portrait')
    assert all(x is not None for x in m), '回退后不应有空洞: %r' % m
    assert all(x['orientation'] == 'landscape' for x in m), \
        '整桶无竖屏时应回退横屏，实际: %r' % [x['orientation'] for x in m]
    # 回退要带走整桶（3 条），不是只加 1 条 → 不同语义块应取到不同素材
    fps = _block_first_paths(blocks, m)
    assert len(set(fps)) > 1, \
        '回退池应含该桶全部反向素材（轮转可换），实际各块: %r' % fps


def test_no_fallback_when_same_orientation_exists():
    """factory 同时有竖屏与横屏 → 只用竖屏（不因回退逻辑而混入横屏）。"""
    items = ([_item('p%d.mp4' % i, 'factory', 'portrait') for i in range(2)]
             + [_item('l%d.mp4' % i, 'factory', 'landscape') for i in range(4)])
    blocks, m = _run(items, 'portrait')
    assert all(x is not None for x in m)
    assert all(x['orientation'] == 'portrait' for x in m), \
        '同方向有素材时不应使用反向素材: %r' % [x['orientation'] for x in m]


def test_landscape_draft_symmetric():
    """对称：只有竖屏时，横版成片也回退用竖屏。"""
    items = [_item('p%d.mp4' % i, 'factory', 'portrait') for i in range(3)]
    blocks, m = _run(items, 'landscape')
    assert all(x is not None for x in m)
    assert all(x['orientation'] == 'portrait' for x in m)


def test_unlabeled_unaffected():
    """无标注素材本就通用，两个方向都能用，不受影响。"""
    items = [_item('u%d.mp4' % i, 'factory', None) for i in range(3)]
    blocks, m = _run(items, 'portrait')
    assert all(x is not None and x['orientation'] is None for x in m)


if __name__ == '__main__':
    test_fallback_when_no_same_orientation()
    test_no_fallback_when_same_orientation_exists()
    test_landscape_draft_symmetric()
    test_unlabeled_unaffected()
    print('OK: 方向回退 4 项断言全部通过')
