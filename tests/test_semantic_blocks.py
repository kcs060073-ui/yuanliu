# -*- coding: utf-8 -*-
"""语义块切分回归基线（P0-①，安全网）。

为什么先有这个文件
------------------
用户拍板的改造顺序：**先内置回归（①），再外置语义配置（②）**。
语义规则一旦外置成 config/semantics.json，15 人团队人人可改 ——
改错一个词不会崩溃，只会**静默产出错画面**（比如"水电蒸汽"被归到宿舍素材），
比崩溃难查得多。所以必须先有这张网：任何人改完配置跑一下它，
块数/类型对不上就说明改坏了，立刻可见。

本文件固化 5 条用户定稿脚本的**期望块数与类型序列**，基线：
    苏州三连否 12 / 苏州没想到 10 / 淮南招商 9 / 嘉定 10 / 乌鲁木齐 8

用法
----
    python tests/test_semantic_blocks.py            # 断言模式（默认，CI 用）
    python tests/test_semantic_blocks.py --print    # 打印当前实际序列（核对/重建基线用）
    pytest tests/test_semantic_blocks.py            # 也可被 pytest 收集

基线变了怎么办
--------------
**不要直接改期望值去迁就代码。** 先跑 --print 看实际序列，
人工确认新序列是对的（用户认可的新切分），再改 BASELINE；
如果新序列是错的，那是回归，去修代码/配置。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
SCRIPTS_DIR = os.path.join(SKILL_DIR, 'scripts')
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import material_matcher as mm                      # noqa: E402
from subtitle_split import split_subtitles          # noqa: E402

CONFIG_DIR = os.path.join(SKILL_DIR, 'config')


def _load_styles():
    import json
    with open(os.path.join(CONFIG_DIR, 'default_styles.json'), encoding='utf-8') as f:
        return json.load(f)['styles']


# ---------------------------------------------------------------------------
# 基线：5 条用户定稿脚本
#   style 1 = 三连否/没想到，style 2 = 招商（见 config/default_styles.json）
# ---------------------------------------------------------------------------
BASELINE = [
    {
        'name': '苏州三连否',
        'style': '1',
        'expect_n': 12,
        'expect_types': [
            'opening', 'exterior', 'exterior', 'location', 'exterior',
            'interior_area', 'interior_struct', 'interior_fac', 'support',
            'price', 'policy', 'cta',
        ],
        'text': '在苏州，租金4毛的设备制造厂房你了解过吗？不是老旧厂房，也不是畸形厂房，'
                '更不是私人厂房！是全新高标准的国资厂房，园区就在长江边上，南邻上海，'
                '10公里内有港口、高速、铁路；现建有多栋全新高标厂房，超多户型可选，可整租，'
                '可分租，厂房层高4.5米-10.8米，一楼可配10吨行车，水、电、蒸汽配套完善，'
                '还配有办公楼、食堂、宿舍等，满足企业生产生活所需，这样的厂房租金仅需4毛，'
                '现在入驻园区还能享租金和产业方面的扶持政策。点击链接，填写信息，预约实地考察',
    },
    {
        'name': '苏州没想到',
        'style': '1',
        'expect_n': 10,
        'expect_types': [
            'opening', 'location', 'exterior',
            'interior_area', 'interior_struct', 'interior_fac', 'support',
            'price', 'policy', 'cta',
        ],
        'text': '真没想到！苏州还有日租金4毛的全新国资厂房！还能享超长免租期！'
                '园区就在长江边上，南邻上海，10公里内有港口、高速、铁路；'
                '现建有多栋全新高标厂房，超多户型可选，可整租，可分租，'
                '厂房层高4.5米-10.8米，一楼可配10吨行车，水、电、蒸汽配套完善，'
                '还配有办公楼、食堂、宿舍等，满足企业生产生活所需，这样的厂房租金仅需4毛，'
                '现在入驻园区还能享租金和产业方面的扶持政策。点击链接，填写信息，预约实地考察',
    },
    {
        'name': '淮南招商',
        'style': '2',
        'expect_n': 9,
        'expect_types': [
            'opening', 'price', 'location', 'exterior',
            'interior_struct', 'interior_fac', 'support', 'policy', 'cta',
        ],
        'text': '安徽淮南产业园火热招商中，厂房租金仅需3元每平每月起，享超长免租期；'
                '园区紧邻合肥，30分钟高铁直达，高速口高铁站都在园区内；'
                '现新建有单层钢结构和多层框架厂房共15万方，层高10~15米，可配10吨航车，'
                '水、电、蒸汽配套完善，还配有办公楼、食堂、宿舍等，满足企业生产生活所需，'
                '现在入驻园区还能享租金和产业方面的扶持政策。点击链接，预约实地考察',
    },
    {
        'name': '嘉定',
        'style': '2',
        'expect_n': 10,
        'expect_types': [
            'opening', 'location', 'exterior', 'exterior',
            'interior_area', 'interior_struct', 'interior_fac',
            'location', 'policy', 'cta',
        ],
        'text': '上海嘉定产业园盛大开盘，邻地铁工业104地块，2026年全新交付的高品质厂房，'
                '现在咨询即享预招商租金优惠；园区厂房及配套楼共6万方，厂房1000平起租，'
                '办公研发楼100平起租，1楼层高10.9米，地面承重5吨，可配5-20吨行车，丙类消防，'
                '满足各类企业生产、研发、办公需求。园区位于工业一零四地块，地铁11号线直达，'
                '5公里上高速，距虹桥枢纽30分钟，1小时车程覆盖上海及苏州核心板块，'
                '物流交通十分便捷；现在入驻园区还能享租金、人才、研发费用和产业方面的扶持政策。'
                '点击链接，预约实地考察',
    },
    {
        'name': '乌鲁木齐',
        'style': '1',
        'expect_n': 8,
        'expect_types': [
            'opening', 'exterior', 'exterior', 'interior_fac', 'support',
            'interior_area', 'interior_struct', 'cta',
        ],
        'text': '租金3毛钱？在乌鲁木齐租厂房只要3毛钱？是的！乌鲁木齐两河产业园，'
                '33栋全新厂房，火热招商中！厂房自带大环评+污水处理，水电齐全、冬季集中供暖，'
                '食堂、宿舍、车位、充电桩等配套也是齐全的，生产生活非常便利；'
                '高规格厂房，500㎡起租，层高4.5-8.1米，承重1吨，做生产、做仓储，'
                '都非常合适，点击链接填写信息，预约实地考察园区。',
    },
]

# ---------------------------------------------------------------------------
# 单句打标回归：块序列相同但单句判错，也可能是坏改动，故再钉一层
# （每条都对应一条用户现场拍板过的规则，改配置时最容易踩）
# ---------------------------------------------------------------------------
TAG_CASES = [
    # CTA / 政策 / 价格 优先级
    ('点击链接，填写信息，预约实地考察', 'cta'),
    ('现在入驻园区还能享租金和产业方面的扶持政策', 'policy'),
    ('这样的厂房租金仅需4毛', 'price'),
    # 位置信号词（"边上/位于/紧邻" 自动命中，不再逐个补城市名）
    ('杭州边上还有这么划算的厂房', 'location'),
    ('园区位于工业一零四地块', 'location'),
    ('而且位置也很好', 'location'),
    # 基础设施（水电/蒸汽/供暖/消防）必须压过 support 的"配套完善"
    ('水、电、蒸汽配套完善', 'interior_fac'),
    ('水电齐全、冬季集中供暖', 'interior_fac'),
    ('丙类消防', 'interior_fac'),
    # 配套
    ('还配有办公楼、食堂、宿舍等', 'support'),
    ('人才公寓精装拎包入住', 'support'),
    ('车位、充电桩等配套也是齐全的', 'support'),
    # 内部面积 / 结构
    ('厂房1000平起租', 'interior_area'),
    ('超多户型可选', 'interior_area'),
    ('1楼层高10.9米，地面承重5吨', 'interior_struct'),
    # "生产" 后跟 "厂房" = 建筑类型 → 外部；不跟厂房 = 用途 → 内部
    ('8栋生产厂房', 'exterior'),
    ('做生产、做仓储', 'interior_struct'),
    # 规模/资质句 = 外部（"15万方"不判面积）
    ('现新建有单层钢结构和多层框架厂房共15万方', 'exterior'),
    ('厂房自带大环评+污水处理', 'exterior'),
]

# location 动态选桶：文案讲什么交通，就先取哪个桶
LOCATION_BUCKET_CASES = [
    ('城际轻轨直达杭州', 'metro'),
    ('地铁11号线直达', 'metro'),
    # 2026-09-14：高速 highway 与公路 traffic 合并成一个桶（用户：命名不规范，是同一个意思）
    ('5公里上高速', 'traffic'),
    ('高铁站就在园区内', 'railway'),
    # 多交通词并存时，当前实现按 TRAFFIC_BUCKET_HINTS 的固定顺序取首个命中
    # （metro→railway→traffic），不按文案里的出现顺序 —— 已知边界，
    # 待用户拍板是否改为"按文案出现顺序"。在此之前这条用例钉住现状，防止误改。
    ('10公里内有港口、高速、铁路', 'railway'),
]

# support 块动态选桶（2026-09-14 新增）：句子里提到什么就先取什么，
# 都没提到才回退固定顺序（仍以宿舍打头）。
SUPPORT_BUCKET_CASES = [
    ('园区自带食堂和餐厅', 'canteen'),
    ('办公楼和写字楼就在旁边', 'office'),
    ('园区绿化和环境很好', 'environment'),
    ('车位充电桩等配套也是齐全的', 'environment'),
    ('宿舍是四人间带独立卫浴', 'dormitory'),
    ('还配有办公楼、食堂、宿舍等', 'canteen'),   # 多词并存时按 hints 顺序：食堂在办公前
]

# 素材侧归类（2026-09-14 起**只用目录命中**，文件名不再兜底）：
# (目录链, 文件名, 期望桶)
MATERIAL_CLASSIFY_CASES = [
    (['厂房内部', '横屏'], 'DJI_20260813_0123_D', 'inside'),
    (['厂房外部', '横板'], 'DJI_20260512_0844_D', 'factory'),
    (['门口', '横屏'], 'DJI_20260813_1543_D', 'entrance'),
    (['公路', '竖屏'], '高速-横版', 'traffic'),
    (['专业设施', '横板'], 'DJI_20260512_0844_D', None),   # DJI 不再等于航拍
    (['地理位置'], 'DJI_20250807_1150_D', None),
    (['新疆园区'], '园区环境-横版', None),                  # 父目录含"园区"不再误判 environment
    ([], '厂房内部_横版', None),                            # 文件名不再兜底
    ([], '交通_横版', None),
    (['宿舍'], '小宿舍-横版', 'dormitory'),
    (['两河园区', '全景', '横屏'], 'DJI_x', 'aerial'),        # 子园区名层级跳过
    (['两河园区', '未知名'], 'DJI_x', None),
]


# ---------------------------------------------------------------------------
# 断言
# ---------------------------------------------------------------------------
def blocks_of(text, style_cfg):
    subs = split_subtitles(text, style_cfg, no_tts=True)
    return subs, mm.split_semantic_blocks(subs)


def check_coverage(blocks, n_subs, name, errs):
    """引擎不变式：块序号必须严格、完整、有序地覆盖所有字幕。

    这条比块数更强：块数对了但漏掉某条字幕（或重复覆盖）也能抓出来 ——
    漏字幕在成片上表现为"某句没有画面"，肉眼很难在十几段里发现。
    """
    flat = [i for b in blocks for i in b['idxs']]
    if flat != list(range(n_subs)):
        errs.append('%s: 字幕覆盖不完整 flat=%s 期望 0..%d'
                    % (name, flat[:20], n_subs - 1))
    for b in blocks:
        if not b['idxs']:
            errs.append('%s: 出现空块 type=%s' % (name, b['type']))


def run_checks(verbose=True):
    styles = _load_styles()
    errs = []
    detail = []

    for case in BASELINE:
        subs, blocks = blocks_of(case['text'], styles[case['style']])
        types = [b['type'] for b in blocks]
        detail.append((case['name'], types))
        if len(blocks) != case['expect_n']:
            errs.append('%s: 块数 %d != 期望 %d' % (case['name'], len(blocks), case['expect_n']))
        if types != case['expect_types']:
            errs.append('%s: 类型序列不符\n    实际 %s\n    期望 %s'
                        % (case['name'], types, case['expect_types']))
        check_coverage(blocks, len(subs), case['name'], errs)

    for text, want in TAG_CASES:
        got = mm.tag_subtitle(text)
        if got != want:
            errs.append('tag_subtitle(%r): 得到 %s，期望 %s' % (text, got, want))

    for text, want in LOCATION_BUCKET_CASES:
        got = mm.location_buckets(text)[0]
        if got != want:
            errs.append('location_buckets(%r)[0]: 得到 %s，期望 %s' % (text, got, want))

    for text, want in SUPPORT_BUCKET_CASES:
        got = mm.support_buckets(text)[0]
        if got != want:
            errs.append('support_buckets(%r)[0]: 得到 %s，期望 %s' % (text, got, want))

    for chain, fname, want in MATERIAL_CLASSIFY_CASES:
        got = mm.classify_with_dir(chain, fname)
        if got != want:
            errs.append('classify_with_dir(%r, %r): 得到 %s，期望 %s'
                        % (chain, fname, got, want))

    if verbose:
        for name, types in detail:
            print('  %-10s %2d 块  %s' % (name, len(types), ' → '.join(types)))
    return errs


def print_baseline():
    """打印当前实际序列，用于人工核对或重建基线。"""
    styles = _load_styles()
    for case in BASELINE:
        subs, blocks = blocks_of(case['text'], styles[case['style']])
        types = [b['type'] for b in blocks]
        print('%s  n=%d' % (case['name'], len(types)))
        print('   ', ' → '.join(types))


# --- pytest 入口 ---
def test_semantic_block_baseline():
    errs = run_checks(verbose=False)
    assert not errs, '语义块回归失败:\n' + '\n'.join(errs)


def test_tag_subtitle_cases():
    errs = []
    for text, want in TAG_CASES:
        got = mm.tag_subtitle(text)
        if got != want:
            errs.append('tag_subtitle(%r): %s != %s' % (text, got, want))
    assert not errs, '\n'.join(errs)


def test_location_bucket_cases():
    errs = []
    for text, want in LOCATION_BUCKET_CASES:
        got = mm.location_buckets(text)[0]
        if got != want:
            errs.append('location_buckets(%r)[0]: %s != %s' % (text, got, want))
    for text, want in SUPPORT_BUCKET_CASES:
        got = mm.support_buckets(text)[0]
        if got != want:
            errs.append('support_buckets(%r)[0]: %s != %s' % (text, got, want))
    for chain, fname, want in MATERIAL_CLASSIFY_CASES:
        got = mm.classify_with_dir(chain, fname)
        if got != want:
            errs.append('classify_with_dir(%r, %r): %s != %s' % (chain, fname, got, want))
    assert not errs, '\n'.join(errs)


def main():
    if '--print' in sys.argv:
        print_baseline()
        return 0
    print('语义块回归基线（5 条定稿脚本 + 单句打标 + 位置选桶）')
    errs = run_checks(verbose=True)
    print()
    if errs:
        print('=' * 56)
        print('FAIL —— 共 %d 处不符（改了语义规则请先确认是不是预期变化）' % len(errs))
        for e in errs:
            print('  ✗', e)
        print('=' * 56)
        return 1
    print('=' * 56)
    print('PASS —— 5 条脚本块数 12/10/9/10/8 与类型序列全部一致')
    print('        单句打标 %d 条、位置选桶 %d 条、配套选桶 %d 条、素材归类 %d 条 全部一致'
          % (len(TAG_CASES), len(LOCATION_BUCKET_CASES), len(SUPPORT_BUCKET_CASES),
             len(MATERIAL_CLASSIFY_CASES)))
    print('=' * 56)
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
