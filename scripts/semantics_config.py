# -*- coding: utf-8 -*-
"""语义规则外置配置的加载与校验（P0-②）。

为什么外置
----------
改一个语义词（比如新增"人才驿站"到配套）原本要改 material_matcher.py 再重发 15 份。
外置后改 config/semantics.json 即可，人人可改。

为什么必须带校验
----------------
外置最大的风险不是报错，是**静默失效**：
桶名拼错（hightway）、关键词组写空、bucket_order 漏一个桶 ——
这些都不会让程序崩溃，只会让画面配错（"水电蒸汽"配到宿舍素材），
成片已经导出才发现，比崩溃难查得多。
所以本模块对配置做**结构化校验**，任何一项不合格就 fail loud（抛异常），
绝不用默认值悄悄兜过去。

为什么必须和回归测试成对
------------------------
能改配置 + 没有回归 = 比不能改更危险。改完配置请跑：
    python tests/test_semantic_blocks.py

用法
----
    from semantics_config import get_semantics
    S = get_semantics()                 # 已校验（首次加载时校验，之后缓存）

    python semantics_config.py --check  # 人工自检：打印校验结果 + 规则摘要
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(os.path.dirname(HERE), 'config')
DEFAULT_PATH = os.path.join(CONFIG_DIR, 'semantics.json')


class SemanticsError(Exception):
    """配置缺失或校验不通过。消息已包含具体错误项，直接展示给用户即可。"""


# 必填的关键词组（值必须是非空字符串列表）
_REQUIRED_KEYWORD_GROUPS = (
    'cta', 'policy', 'price',
    'location_markers', 'location_closed',
    'support',
    'interior_area', 'interior_struct', 'interior_fac', 'interior_fac_strict',
    'exterior', 'park',
)

# 必填的块类型（block_buckets 的键）
_REQUIRED_BLOCK_TYPES = (
    'opening', 'exterior', 'interior', 'interior_spec',
    'interior_area', 'interior_struct', 'interior_fac',
    'location', 'support', 'price', 'policy', 'cta',
)


def _err(msg):
    raise SemanticsError(msg)


def validate(cfg, path='<memory>'):
    """校验配置结构，发现问题抛 SemanticsError（收集全部错误后一次性抛出）。"""
    errs = []

    def where(field):
        return '%s → %s' % (os.path.basename(path), field)

    if not isinstance(cfg, dict):
        _err('%s：配置根节点必须是 JSON 对象' % where('<root>'))

    # ---- bucket 体系（顺序敏感：具体 → 通用）----
    order = cfg.get('bucket_order')
    if not isinstance(order, list) or not order:
        errs.append(where('bucket_order') + '：必须是非空数组')
        order = []
    elif len(set(order)) != len(order):
        errs.append(where('bucket_order') + '：存在重复桶名')

    sbs = cfg.get('semantic_buckets')
    if not isinstance(sbs, dict) or not sbs:
        errs.append(where('semantic_buckets') + '：必须是非空对象')
        sbs = {}
    else:
        for b, kws in sbs.items():
            if not isinstance(kws, list) or not kws:
                errs.append(where('semantic_buckets.%s') % b + '：必须是非空数组')
            elif not all(isinstance(k, str) for k in kws):
                errs.append(where('semantic_buckets.%s') % b + '：数组元素必须是字符串')
    if order and sbs:
        missing = [b for b in order if b not in sbs]
        extra = [b for b in sbs if b not in order]
        if missing:
            errs.append(where('bucket_order') + '：%s 在 semantic_buckets 里没有定义' % missing)
        if extra:
            errs.append(where('semantic_buckets') + '：%s 没写进 bucket_order（永不生效）' % extra)

    known_buckets = set(order) | set(sbs)

    def _check_bucket_names(field, names):
        bad = [b for b in names if b not in known_buckets]
        if bad:
            errs.append(where(field) + '：桶名 %s 不存在（拼错？可用桶：%s）'
                        % (bad, sorted(known_buckets)))

    # ---- 块类型 → 素材桶 ----
    bbs = cfg.get('block_buckets')
    if not isinstance(bbs, dict) or not bbs:
        errs.append(where('block_buckets') + '：必须是非空对象')
    else:
        for bt in _REQUIRED_BLOCK_TYPES:
            if bt not in bbs:
                errs.append(where('block_buckets') + '：缺少块类型 "%s"' % bt)
        for bt, bks in bbs.items():
            if not isinstance(bks, list) or not bks:
                errs.append(where('block_buckets.%s') % bt + '：必须是非空数组')
                continue
            if not all(isinstance(k, str) for k in bks):
                errs.append(where('block_buckets.%s') % bt + '：数组元素必须是字符串')
                continue
            _check_bucket_names('block_buckets.%s' % bt, bks)

    # ---- 交通词 → 桶 ----
    hints = cfg.get('traffic_bucket_hints')
    if not isinstance(hints, list) or not hints:
        errs.append(where('traffic_bucket_hints') + '：必须是非空数组')
    else:
        for i, h in enumerate(hints):
            fld = 'traffic_bucket_hints[%d]' % i
            if not isinstance(h, dict):
                errs.append(where(fld) + '：必须是 {"keywords": [...], "bucket": "..."}')
                continue
            kws = h.get('keywords')
            if not isinstance(kws, list) or not kws:
                errs.append(where(fld + '.keywords') + '：必须是非空数组')
            bk = h.get('bucket')
            if not isinstance(bk, str) or not bk:
                errs.append(where(fld + '.bucket') + '：必须是非空字符串')
            elif known_buckets:
                _check_bucket_names(fld + '.bucket', [bk])

    # ---- 配套词 → 桶（2026-09-14 新增；可选，缺省 = support 块走固定顺序）----
    shints = cfg.get('support_bucket_hints')
    if shints is not None:
        if not isinstance(shints, list):
            errs.append(where('support_bucket_hints') + '：必须是数组')
        else:
            for i, h in enumerate(shints):
                fld = 'support_bucket_hints[%d]' % i
                if not isinstance(h, dict):
                    errs.append(where(fld) + '：必须是 {"keywords": [...], "bucket": "..."}')
                    continue
                kws = h.get('keywords')
                if not isinstance(kws, list) or not kws:
                    errs.append(where(fld + '.keywords') + '：必须是非空数组')
                bk = h.get('bucket')
                if not isinstance(bk, str) or not bk:
                    errs.append(where(fld + '.bucket') + '：必须是非空字符串')
                elif known_buckets:
                    _check_bucket_names(fld + '.bucket', [bk])

    # ---- 禁止跨园区的桶 ----
    ncb = cfg.get('no_cross_buckets')
    if not isinstance(ncb, list):
        errs.append(where('no_cross_buckets') + '：必须是数组（不需要可写 []）')
    elif ncb and known_buckets:
        _check_bucket_names('no_cross_buckets', ncb)

    # ---- 关键词组 ----
    kws_root = cfg.get('keywords')
    if not isinstance(kws_root, dict):
        errs.append(where('keywords') + '：必须是对象')
    else:
        for g in _REQUIRED_KEYWORD_GROUPS:
            v = kws_root.get(g)
            if v is None:
                errs.append(where('keywords.%s') % g + '：缺失')
            elif not isinstance(v, list) or not v:
                errs.append(where('keywords.%s') % g + '：必须是非空数组'
                            '（写空会让该语义永不命中，属静默失效）')
            elif not all(isinstance(k, str) and k for k in v):
                errs.append(where('keywords.%s') % g + '：数组元素必须是非空字符串')

    # ---- 阈值 ----
    th = cfg.get('thresholds')
    if not isinstance(th, dict):
        errs.append(where('thresholds') + '：必须是对象')
    elif not isinstance(th.get('short_exterior_len'), int):
        errs.append(where('thresholds.short_exterior_len') + '：必须是整数')

    if errs:
        raise SemanticsError(
            '语义配置校验失败（共 %d 处）。为避免产出错误画面，程序已停止；'
            '修好 config/semantics.json 后重跑。\n  - ' % len(errs) + '\n  - '.join(errs))
    return True


_CACHE = {}


def load(path=None, force=False):
    """加载并校验配置。返回原始 dict。路径缺省用 config/semantics.json。"""
    p = os.path.abspath(path or DEFAULT_PATH)
    if not force and p in _CACHE:
        return _CACHE[p]
    if not os.path.isfile(p):
        raise SemanticsError(
            '找不到语义配置文件：%s\n'
            '  该文件随 skill 一起分发，缺失说明安装包不完整 —— 请重新获取后重试。\n'
            '  （不使用内置默认值兜底：悄悄用旧规则出片，比停下来更难查）' % p)
    try:
        with open(p, encoding='utf-8') as f:
            cfg = json.load(f)
    except UnicodeDecodeError:
        raise SemanticsError('语义配置不是 UTF-8 编码：%s' % p)
    except json.JSONDecodeError as e:
        raise SemanticsError('语义配置 JSON 语法错误：%s\n  %s' % (p, e))
    validate(cfg, p)
    _CACHE[p] = cfg
    return cfg


def get_semantics(path=None):
    """取"代码可直接用"的语义规则对象（已展开组合、类型规范化）。"""
    cfg = load(path)
    kws = cfg['keywords']
    return {
        'bucket_order': list(cfg['bucket_order']),
        'semantic_buckets': {k: list(v) for k, v in cfg['semantic_buckets'].items()},
        'block_buckets': {k: list(v) for k, v in cfg['block_buckets'].items()},
        # tuple 化：分块算法里当有序常量用
        'traffic_bucket_hints': tuple(
            (tuple(h['keywords']), h['bucket']) for h in cfg['traffic_bucket_hints']),
        'no_cross_buckets': tuple(cfg['no_cross_buckets']),
        'cta': list(kws['cta']),
        'policy': list(kws['policy']),
        'price': list(kws['price']),
        # 居住实体词（2026-09-16 新增，方案 A）：在引擎里排在价格词之前判定 ——
        # "精装人才公寓月租500元起"里的"500元"命中价格词，会把整句拖进价格块。
        # 老配置没有这个键时给空表 = 保持旧行为（不跳错、不静默改画面）。
        'residence': list(kws.get('residence') or []),
        # location = 封闭词 + 信号词（信号词机制：不必逐个补城市名）
        'location': list(kws['location_closed']) + list(kws['location_markers']),
        'location_markers': list(kws['location_markers']),
        'support': list(kws['support']),
        'interior_area': list(kws['interior_area']),
        'interior_struct': list(kws['interior_struct']),
        'interior_fac': list(kws['interior_fac']),
        'interior_fac_strict': list(kws['interior_fac_strict']),
        'interior': (list(kws['interior_area']) + list(kws['interior_struct'])
                     + list(kws['interior_fac'])),
        'exterior': list(kws['exterior']),
        'park': list(kws['park']),
        'short_exterior_len': cfg['thresholds']['short_exterior_len'],
        # support 块动态排序（2026-09-14 新增；老配置没有这个键时给空表 = 保持旧行为）
        'support_bucket_hints': tuple(
            (tuple(h['keywords']), h['bucket'])
            for h in (cfg.get('support_bucket_hints') or [])),
    }


def summarize(cfg=None):
    """自检摘要：让人一眼看出配置是否"看起来对"。"""
    cfg = cfg or load()
    kws = cfg['keywords']
    lines = [
        '语义配置：%s' % DEFAULT_PATH,
        '  素材桶      %d 个：%s' % (len(cfg['bucket_order']), ' → '.join(cfg['bucket_order'])),
        '  块类型      %d 个：%s' % (len(cfg['block_buckets']), ', '.join(cfg['block_buckets'])),
        '  交通 hint   %d 条' % len(cfg['traffic_bucket_hints']),
        '  禁跨园区桶  %s' % (', '.join(cfg['no_cross_buckets']) or '(无)'),
        '  关键词组    %s' % ', '.join(
            '%s(%d)' % (g, len(kws[g])) for g in _REQUIRED_KEYWORD_GROUPS),
        '  居住实体词  %s（先于价格词判定）'
        % (', '.join(kws.get('residence') or []) or '(未配置，按旧行为)'),
        '  短外部句阈值 %d 字' % cfg['thresholds']['short_exterior_len'],
    ]
    return '\n'.join(lines)


def main():
    if '--check' in sys.argv or not sys.argv[1:]:
        try:
            cfg = load()
            validate(cfg, DEFAULT_PATH)
        except SemanticsError as e:
            print('[FAIL]', e)
            return 1
        print('[OK] 语义配置校验通过')
        print(summarize(cfg))
        print()
        print('改完配置请跑回归：python tests/test_semantic_blocks.py')
        return 0
    if '--dump' in sys.argv:
        print(json.dumps(load(), ensure_ascii=False, indent=2))
        return 0
    print(__doc__)
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
