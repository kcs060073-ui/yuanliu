# -*- coding: utf-8 -*-
"""方案 A 回归：居住实体词优先于价格词（2026-09-16 用户拍板）。

背景
    新仓/嘉定脚本「精装人才公寓月租500元起」里的"500元"命中价格词「元」。
    引擎按写死的顺序判定 cta > policy > price > … > support，整句被拖进价格块；
    价格块的素材桶是「厂房外部 → 厂房内部」，于是画面配到厂房素材、
    永远不查宿舍桶（用户报：宿舍那个语义块没筛选到宿舍素材，却还是厂房素材）。

修复
    新增 keywords.residence（宿舍/公寓/人才公寓/住宿），引擎里**排在价格词之前**判定。
    只影响"含居住实体词又带金额"的句子；真价格句（厂房租金3毛起 / 18元/㎡）不受影响。

用法：
    python tests/test_residence_priority.py
    pytest tests/test_residence_priority.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
SCRIPTS_DIR = os.path.join(SKILL_DIR, 'scripts')
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import material_matcher as mm                          # noqa: E402

# 关掉园区锁定（本测试用合成素材，不依赖真实目录 / park_rules）
mm._pr = None


def _item(path, bucket):
    return {'path': path, 'name': os.path.basename(path), 'bucket': bucket,
            'kind': 'video', 'duration': 10_000_000, 'orientation': None,
            'dir_chain': []}


def _run(texts, items, seed=7):
    """喂合成素材跑一次真实选片，返回 matched 列表。"""
    mm.scan_materials = lambda *a, **k: list(items)
    subs = [{'text': t} for t in texts]
    durs = [2_000_000] * len(texts)
    return mm.match_materials(subs, 'FAKE_DIR', sub_durations=durs,
                              seed=seed, video_orientation='portrait')


def _three_bucket_items():
    return [_item('D:/fake/aerial/pan.mp4', 'aerial'),
            _item('D:/fake/dormitory/dorm.mp4', 'dormitory'),
            _item('D:/fake/factory/plant.mp4', 'factory')]


# ---------------------------------------------------------------- 判定层
def test_residence_beats_price():
    """含居住实体词的句子归 support；真价格句仍是 price（不能被误伤）。"""
    assert mm.tag_subtitle('精装人才公寓月租500元起') == 'support'
    assert mm.tag_subtitle('宿舍月租500元') == 'support'
    assert mm.tag_subtitle('人才公寓拎包入住') == 'support'
    assert mm.tag_subtitle('配套精装人才公寓') == 'support'
    # 真价格句：不含居住实体词 → 维持 price
    assert mm.tag_subtitle('这样的厂房租金仅需3毛起') == 'price'
    assert mm.tag_subtitle('装修免租期可谈3-6个月') == 'price'
    assert mm.tag_subtitle('租金低至18元/㎡起！') == 'price'
    assert mm.tag_subtitle('镇区房价仅5000-6000元/㎡') == 'price'
    # 其他既有语义不受影响
    assert mm.tag_subtitle('水电蒸汽配套完善') == 'interior_fac'
    assert mm.tag_subtitle('层高10.9米承重5吨') == 'interior_struct'


def test_support_bucket_prefers_dormitory():
    """support 块里宿舍仍排第一（不会被 support_bucket_hints 改写）。"""
    assert mm.support_buckets('精装人才公寓月租500元起')[0] == 'dormitory'


def test_backward_compatible_empty_residence():
    """老配置（residence 缺失/为空）时退回旧行为：公寓句又是 price。"""
    saved = mm.RESIDENCE_KWS
    try:
        mm.RESIDENCE_KWS = []
        assert mm.tag_subtitle('精装人才公寓月租500元起') == 'price'
    finally:
        mm.RESIDENCE_KWS = saved


def test_config_exposes_residence():
    """配置里确实有 residence，且引擎读到了。"""
    cfg = json.load(open(os.path.join(SKILL_DIR, 'config', 'semantics.json'),
                         encoding='utf-8'))
    assert cfg['keywords'].get('residence'), 'semantics.json 缺 keywords.residence'
    assert '公寓' in mm.RESIDENCE_KWS
    assert '宿舍' in mm.RESIDENCE_KWS


# ---------------------------------------------------------------- 端到端
def test_end_to_end_apartment_gets_dormitory_clip():
    """端到端：公寓句必须落到宿舍素材，厂房租金句仍落厂房素材。"""
    texts = ['智谷天地产业园',             # 开场（含 park 词，缩短开场块）
             '精装人才公寓月租500元起',    # 修复点：应走宿舍桶
             '这样的厂房租金仅需3毛起']    # 真价格句：仍走厂房桶
    matched = _run(texts, _three_bucket_items())
    assert matched[1] is not None and matched[1]['bucket'] == 'dormitory', \
        '公寓句应配宿舍素材，实际：%r' % (matched[1] or {}).get('path')
    assert matched[2] is not None and matched[2]['bucket'] == 'factory', \
        '厂房租金句应仍配厂房素材，实际：%r' % (matched[2] or {}).get('path')


def test_end_to_end_regression_without_fix():
    """反证：清空 residence（= 修复前），同一脚本公寓句会落到厂房素材。"""
    texts = ['智谷天地产业园', '精装人才公寓月租500元起', '这样的厂房租金仅需3毛起']
    saved = mm.RESIDENCE_KWS
    try:
        mm.RESIDENCE_KWS = []
        matched = _run(texts, _three_bucket_items())
        assert matched[1] is not None and matched[1]['bucket'] == 'factory', \
            '清空 residence 后应退回旧行为（配厂房），实际：%r' % (
                (matched[1] or {}).get('bucket'))
    finally:
        mm.RESIDENCE_KWS = saved


def main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for fn in fns:
        try:
            fn()
        except AssertionError as e:
            print('FAIL %s: %s' % (fn.__name__, e))
            return 1
        except Exception as e:
            print('ERROR %s: %r' % (fn.__name__, e))
            return 1
        print('  PASS %s' % fn.__name__)
    print('OK: %d 项全部通过（居住实体词优先于价格词）' % len(fns))
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
