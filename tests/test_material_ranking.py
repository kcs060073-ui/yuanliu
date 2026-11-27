# -*- coding: utf-8 -*-
"""选片打分回归（2026-11-27 用户反馈：工具匹配不如人工稳、不如人工准）。

锁死四件事：
  ① **确定性**：同一脚本 + 同一素材库，跑两次结果必须完全一样
     （旧实现桶内 shuffle，9 个镜头可以一个都不重样）
  ② **目录提示**：脚本里的词能拉动语义桶之外的目录
     （「上海嘉定产业园」→ 厂牌logo；「在这里开会」→ 会议室）
  ③ **语义归属**：「可整栋、可分层」必须落厂房内部，不走卷帘门、也不兜底成外部
  ④ **同片不复用**：一条成片内素材不重复，池子用完才允许
  ⑤ **单一打分实现**：分镜预览与出片走同一个 score_item

用法：
    python tests/test_material_ranking.py
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


def _item(rel, bucket, dur=6_000_000, kind='video', orientation=None):
    rel = rel.replace('\\', '/')
    dirs = rel.split('/')[:-1]
    return {'path': rel, 'name': os.path.basename(rel), 'bucket': bucket,
            'kind': kind, 'duration': dur, 'dir_chain': dirs,
            'orientation': orientation}


def _mk(texts):
    return [{'text': t} for t in texts]


def _run(texts, items, durs=None):
    mm.scan_materials = lambda *a, **k: list(items)
    durs = durs or [2_000_000] * len(texts)
    return mm.match_materials(_mk(texts), 'FAKE_DIR', sub_durations=durs,
                              video_orientation='landscape', record_usage=False)


def _seq(matched):
    """连续重复合并后的镜头序列（相对路径）。"""
    out, prev = [], None
    for it in matched:
        p = it['path'] if it else None
        if p != prev:
            out.append(p)
        prev = p
    return out


# ---------------------------------------------------------------- ① 确定性
def test_same_input_same_output():
    items = ([_item('厂房外部/横屏/f%d.mp4' % i, 'factory') for i in range(4)]
             + [_item('厂房内部/横屏/i%d.mp4' % i, 'inside') for i in range(4)]
             + [_item('全景/横屏/a%d.mp4' % i, 'aerial') for i in range(3)])
    texts = ['在上海有工厂已经满产想扩容的老板注意',
             '现成高标厂房', '首层承重5吨层高10.9米',
             '厂房内部可分割', '给你精准匹配厂房', '点击链接预约考察']
    a = _seq(_run(texts, items))
    b = _seq(_run(texts, items))
    assert a == b, '同一输入两次结果不一致：\n  %r\n  %r' % (a, b)


# ---------------------------------------------------------------- ② 目录提示
def test_hint_pulls_park_sign():
    """园区名句在开场块之外时，应优先用「厂牌」镜头（桶外目录）。"""
    fn = {'dir_chain': ['厂牌logo'], 'path': '厂牌logo/x.mp4'}
    bonus, hit = mm.hint_bonus('这里是上海嘉定产业园', fn)
    assert bonus > 0 and hit == '厂牌', '产业园→厂牌 提示未生效: %r' % ((bonus, hit),)


def test_hint_survives_long_block():
    """补盲素材比整块短时，按句拆开保住它（hint_floor + split_for_hint 机制回归）。

    用「产业园 → 厂牌」举例：整块 8s，厂牌镜头只有 3s，直接整块匹配会被
    fit_ok 滤掉。块里没有卷帘门了，但这套机制仍然要能工作。
    """
    items = ([_item('厂房外部/横屏/f%d.mp4' % i, 'factory', dur=20_000_000)
              for i in range(4)]
             + [_item('厂牌logo/x%d.mp4' % i, 'environment', dur=3_000_000)
                for i in range(3)])
    texts = ['想扩容的老板注意', '现成高标厂房', '多栋厂房可选',
             '给你精准匹配厂房', '上海嘉定产业园', '点击链接预约考察']
    seq = _seq(_run(texts, items,
                    durs=[2_000_000, 2_000_000, 2_000_000,
                          4_000_000, 4_000_000, 2_000_000]))
    assert any(p.startswith('厂牌logo/') for p in seq), \
        '长块把桶外补盲素材挤掉了: %r' % (seq,)


# ---------------------------------------------------------------- ③ 语义归属
def test_floor_split_stays_inside():
    """「可整栋、可分层」必须落厂房内部（2026-11-27 铠畅拍板）。

    两句坑都踩过：
      ① 曾配「整栋/分层 → 卷帘门」目录提示，画面跑到卷帘门；
      ② 删掉提示后，这句没有任何关键词命中 → 兜底判成 exterior（厂房外部）。
    现在靠 keywords.interior_struct 的「分层/分栋/分割」判成内部规格。
    """
    items = (
        [_item('厂房外部/横屏/f%d.mp4' % i, 'factory') for i in range(3)]
        + [_item('厂房内部/横屏/i%d.mp4' % i, 'inside') for i in range(6)]
        + [_item('卷帘门/横屏/r1.mp4', 'environment')]
    )
    texts = ['想扩容找不到厂房的老板注意', '上海嘉定产业园',
             '首层承重5吨、层高10.9米，可整栋、可分层灵活选择', '点击链接预约考察']
    assert mm.tag_subtitle('可整栋、可分层灵活选择') == 'interior_struct', \
        '「可整栋可分层」语义判定不是内部规格: %s' % mm.tag_subtitle('可整栋、可分层灵活选择')
    seq = _seq(_run(texts, items))
    assert '卷帘门/横屏/r1.mp4' not in seq, '「整栋/分层」不该再选中卷帘门: %r' % (seq,)
    assert any(p.startswith('厂房内部/') for p in seq), \
        '「整栋/分层」没有落在厂房内部: %r' % (seq,)


def test_hint_does_not_hijack_opening():
    """开场块不启用目录提示：园区名被并进开场时不挤掉航拍/门口。"""
    items = (
        [_item('门口/横屏/e1.mp4', 'entrance')]
        + [_item('全景/横屏/a1.mp4', 'aerial')]
        + [_item('厂牌logo/x.mp4', 'environment')]
    )
    texts = ['想扩容的老板注意', '上海嘉定产业园']
    seq = _seq(_run(texts, items))
    assert seq and seq[0] != '厂牌logo/x.mp4', \
        '开场不应被厂牌劫持（应保留门口/航拍）: %r' % (seq,)


def test_opening_prefers_aerial():
    """开场桶序：航拍 → 门口 → 厂房外部（2026-11-27 用户拍板提到第一位）。"""
    items = ([_item('门口/横屏/e%d.mp4' % i, 'entrance') for i in range(2)]
             + [_item('全景/横屏/a%d.mp4' % i, 'aerial') for i in range(2)]
             + [_item('厂房外部/横屏/f%d.mp4' % i, 'factory') for i in range(2)])
    texts = ['想扩容的老板注意', '上海嘉定产业园', '现成高标厂房']
    seq = _seq(_run(texts, items))
    assert seq and seq[0].startswith('全景/'), '开场没有优先航拍: %r' % (seq,)


# ---------------------------------------------------------------- ④ 同片不复用
def test_no_reuse_within_video():
    items = [_item('厂房外部/横屏/f%d.mp4' % i, 'factory') for i in range(5)]
    texts = ['想扩容找不到厂房的老板注意', '上海嘉定产业园', '现成高标厂房',
             '全新高标准厂房', '多栋厂房可选', '点击链接预约考察']
    seq = _seq(_run(texts, items))
    assert len(seq) == len(set(seq)), '同一条成片里出现了重复素材: %r' % (seq,)
    assert len(seq) >= 3, '多段外部应有多个不同镜头，实际只用了 %d 个: %r' % (
        len(seq), seq)


# ---------------------------------------------------------------- ④ 单一打分
def test_storyboard_uses_same_scorer():
    """预览的候选分必须等于出片的打分（同一个 score_item）。"""
    it = _item('厂房外部/横屏/f1.mp4', 'factory', dur=6_000_000)
    s1, _ = mm.score_item(it, '现成高标厂房', 0, 2_000_000)
    import storyboard
    s2, _ = storyboard._candidate_score(it, 2_000_000, 0, 'landscape', {}, '',
                                        '现成高标厂房')
    assert int(round(s1)) == s2, '预览与出片打分不一致: %s vs %s' % (s1, s2)


if __name__ == '__main__':
    test_same_input_same_output()
    test_hint_pulls_park_sign()
    test_hint_survives_long_block()
    test_floor_split_stays_inside()
    test_hint_does_not_hijack_opening()
    test_opening_prefers_aerial()
    test_no_reuse_within_video()
    test_storyboard_uses_same_scorer()
    print('OK: 选片打分 8 项断言全部通过'
          '（确定性 / 目录提示 / 内部归属 / 长块补盲 / 开场航拍 / 同片不复用 / 单一实现）')
