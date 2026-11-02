# -*- coding: utf-8 -*-
"""图片参与选片的段落级约束回归（2026-09-15 用户拍板）。

两条硬规则：
  ① 段落总时长 > 5s → 优先视频素材（静态图铺 5s 太闷）
  ② 同一段落最多用 2 个图片素材（图片是静帧，堆多了像 PPT 翻页）

直接 monkeypatch 扫描层，喂受控的合成素材，跑真实 match_materials 选片路径
（不是另写一份实现），用 seed 固定随机、断言确定性。

用法：
    python tests/test_image_constraint.py
    pytest tests/test_image_constraint.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
SCRIPTS_DIR = os.path.join(SKILL_DIR, 'scripts')
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import material_matcher as mm                          # noqa: E402

# 关掉园区锁定（本测试不依赖真实目录 / park_rules）
mm._pr = None


def _item(path, bucket, kind, duration_us):
    return {'path': path, 'name': os.path.basename(path), 'bucket': bucket,
            'kind': kind, 'duration': duration_us, 'orientation': None,
            'dir_chain': [], 'bucket_dummy': bucket}


def _run(subtitles, items, sub_durations, seed=7):
    """喂合成素材跑一次真实选片，返回 matched 列表。"""
    mm.scan_materials = lambda *a, **k: list(items)
    return mm.match_materials(subtitles, 'FAKE_DIR',
                              sub_durations=sub_durations, seed=seed)


def _mk_subs(texts):
    return [{'text': t} for t in texts]


def _count_images(seq):
    return sum(1 for m in seq if m and m.get('kind') == 'image')


def _block_report(subtitles, sub_durations, matched):
    """按真实语义块切分，返回每块 {type, dur(us), n_img, idxs}。"""
    reps = []
    for b in mm.split_semantic_blocks(subtitles):
        bdur = sum(sub_durations[i] for i in b['idxs']) if sub_durations else 0
        n_img = _count_images([matched[i] for i in b['idxs']])
        reps.append({'type': b['type'], 'dur': bdur, 'n_img': n_img,
                     'idxs': b['idxs']})
    return reps


def test_long_block_prefers_video():
    """段落 > 5s：即使池里有图片，也应全部取视频。"""
    subs = _mk_subs(['嘉兴园区招商', '高标准厂房', '全新国资厂房', '万方厂房任选'])
    # 视频 1s（best_cap=2s）→ 开场块(14s)与 exterior 块(6s) 均 >5s，按字幕拆组，
    # 每组 >5s → 优先视频
    items = (
        [_item('v%d.mp4' % i, 'factory', 'video', 1_000_000) for i in range(4)]
        + [_item('img%d.jpg' % i, 'factory', 'image', 0) for i in range(6)]
    )
    durs = [2_000_000, 6_000_000, 6_000_000, 6_000_000]
    matched = _run(subs, items, durs)
    reps = _block_report(subs, durs, matched)
    # 所有 >5s 的块都不许出现图片
    for r in reps:
        if r['dur'] > mm.LONG_SEG_US:
            assert r['n_img'] == 0, '>5s 段落(%s)不应使用图片，实际: %r' % (r['type'], r)
    assert _count_images(matched) == 0, '全程不应使用图片: %r' % matched


def test_short_block_caps_images_at_two():
    """段落 ≤ 5s：图片可用，但同一段落上限 2 个（其余退视频）。"""
    subs = _mk_subs(['嘉兴园区招商'] + ['厂房%d' % i for i in range(6)])   # 7 条
    # 视频 1s（best_cap=2s），开场块 3.5s / exterior 块 3s 都 ≤5s 不优先视频，
    # 但各拆成多组 → 验证每段图片 ≤2
    items = (
        [_item('v%d.mp4' % i, 'factory', 'video', 1_000_000) for i in range(4)]
        + [_item('img%d.jpg' % i, 'factory', 'image', 0) for i in range(6)]
    )
    durs = [2_000_000] + [750_000] * 6
    matched = _run(subs, items, durs, seed=3)
    reps = _block_report(subs, durs, matched)
    for r in reps:
        # 池里 6 张图都可用；无约束每段会全用图，约束后必须 ≤2（证明上限生效）
        assert r['n_img'] <= 2, '单段落(%s)图片应 ≤2，实际 %d: %r' % (
            r['type'], r['n_img'], r)
    # 反向：图片确实被用上了（否则测不到约束在生效）
    assert _count_images(matched) >= 1, '短段落应允许用图片，约束似乎没生效'


def test_per_block_independent_cap():
    """每段独立计数：两段短 exterior 各最多 2 图，互不挤占（合计 ≤4）。"""
    subs = _mk_subs(['嘉兴园区招商', '高标准厂房', '全新厂房', '万方厂房',
                     '现在租金3毛', '厂房A', '厂房B', '厂房C'])
    items = (
        [_item('v%d.mp4' % i, 'factory', 'video', 1_000_000) for i in range(8)]
        + [_item('img%d.jpg' % i, 'factory', 'image', 0) for i in range(12)]
    )
    durs = [2_000_000, 750_000, 750_000, 750_000, 1_000_000, 750_000, 750_000, 750_000]
    matched = _run(subs, items, durs, seed=11)
    reps = _block_report(subs, durs, matched)
    for r in reps:
        assert r['n_img'] <= 2, '每段图片应 ≤2，实际 %r' % r
    assert _count_images(matched) <= 4, '两段合计应≤4: %r' % matched


def test_no_subdurations_no_regression():
    """不传 sub_durations（旧调用路径）：图片照常可用，不报错、不空洞。"""
    subs = _mk_subs(['嘉兴园区招商', '高标准厂房', '配套完善', '点击链接预约'])
    items = (
        [_item('v%d.mp4' % i, 'factory', 'video', 3_000_000) for i in range(3)]
        + [_item('img%d.jpg' % i, 'factory', 'image', 0) for i in range(3)]
    )
    matched = _run(subs, items, None)   # sub_durations=None
    assert len(matched) == 4
    assert all(m is not None for m in matched), '旧路径不应产出空洞'


if __name__ == '__main__':
    test_long_block_prefers_video()
    test_short_block_caps_images_at_two()
    test_per_block_independent_cap()
    test_no_subdurations_no_regression()
    print('OK: 图片段落约束 4 项断言全部通过')
