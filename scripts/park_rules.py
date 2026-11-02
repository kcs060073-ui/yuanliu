# -*- coding: utf-8 -*-
"""园区规则：园区识别词 + 各语义桶的跨园区通用性。

配置来源：`语义规则分配表.xlsx`（人工编辑）→ `import_rules.py` → `config/park_rules.json`。
代码只读 JSON，Excel 是唯一编辑入口，改完跑一次导入即可生效。

规则要点（2026-09-11 用户拍板）：
1. 选片前必须先锁定园区文件夹，只用该园区的素材；
2. 只有标记为"可跨园区通用"的语义桶，才允许向兄弟园区借素材；
3. 借用时按"通用例外"排除指定园区（如宿舍排除新疆园区）；
4. 识别不到园区就拦停，**绝不退化成多园区大混池**。
"""
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
CONFIG_PATH = os.path.join(_ROOT, 'config', 'park_rules.json')

# 兜底默认：config 缺失/损坏也能跑，且与 Excel 首版内容一致
DEFAULT_RULES = {
    'version': 1,
    'updated': '2026-09-11',
    'parks': {
        '北京园区': {'display': '北京', 'aliases': ['北京', '医械城'], 'nearby': ['德州园区']},
        '嘉兴园区': {'display': '嘉兴', 'aliases': ['嘉兴'],
                     'nearby': ['新仓园区', '嘉定园区', '苏州园区']},
        '新疆园区': {'display': '两河园区', 'aliases': ['乌鲁木齐', '新疆', '两河园区', '两河'],
                     'nearby': []},
        '苏州园区': {'display': '苏州', 'aliases': ['苏州'],
                     'nearby': ['嘉定园区', '嘉兴园区', '新仓园区', '南京园区']},
        '贵阳园区': {'display': '贵阳', 'aliases': ['贵阳', '贵州'], 'nearby': []},
        '南京园区': {'display': '南京', 'aliases': ['南京', '南京园区'],
                     'nearby': ['苏州园区', '嘉定园区', '德州园区']},
        '嘉定园区': {'display': '嘉定', 'aliases': ['嘉定', '上海嘉定', '嘉定园区'],
                     'nearby': ['苏州园区', '嘉兴园区', '新仓园区']},
        '德州园区': {'display': '德州', 'aliases': ['德州', '山东德州', '德州园区'],
                     'nearby': ['北京园区', '南京园区']},
        '新仓园区': {'display': '新仓', 'aliases': ['新仓园区', '平湖新仓'],
                     'nearby': ['嘉兴园区', '嘉定园区', '苏州园区']},
    },
    'buckets': {
        'inside': {'cross': True, 'exclude': [], 'fallback': '借用其他园区厂房内部'},
        'dormitory': {'cross': True, 'exclude': ['新疆园区'], 'fallback': '借用其他园区宿舍（不含新疆）'},
    },
}

_CACHE = {}


def load(force=False):
    """读取配置；缺失/损坏时用默认值，并把默认值写回磁盘便于用户查看。"""
    if _CACHE and not force:
        return _CACHE
    rules = None
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding='utf-8') as f:
                rules = json.load(f)
        except Exception:
            rules = None
    if not isinstance(rules, dict) or 'parks' not in rules:
        rules = json.loads(json.dumps(DEFAULT_RULES))
        try:
            save(rules)
        except Exception:
            pass
    _CACHE.clear()
    _CACHE.update(rules)
    return _CACHE


def save(rules):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)
    _CACHE.clear()
    _CACHE.update(rules)


def _normalize_parks(rules):
    """补齐字段时间戳：priority 缺省 1，nearby 缺省 []。"""
    for folder, info in (rules.get('parks') or {}).items():
        if isinstance(info, dict):
            info.setdefault('priority', 1)
            info.setdefault('nearby', [])
    return rules


def _park_names(folder, info):
    """给 --park 用：文件夹名 + 显示名 + 别名，都接受。"""
    names = [folder]
    if info.get('display'):
        names.append(info['display'])
    names += [a for a in (info.get('aliases') or []) if a]
    return names


def script_keywords(folder, info):
    """给"从脚本正文识别园区"用：**只认表里 C 列的识别词（aliases）**。

    2026-09-14 修正：以前这里把"显示名"也当关键词，短显示名会误命中 ——
    实测 `新仓园区`（显示名"新仓"）会把脚本里的"全**新仓**库""新仓库"识别成园区名。
    现在脚本地名完全由 Excel「园区识别词表」C 列控制；显示名只用于 `--park`
    匹配与草稿命名，不参与脚本正文识别。
    """
    seen, out = set(), []
    for a in (info.get('aliases') or []):
        a = (a or '').strip()
        if a and a not in seen:
            seen.add(a)
            out.append(a)
    return out


def looks_like_root(materials_dir):
    """判断是不是素材总根（下面直接挂着多个园区文件夹）。"""
    if not materials_dir or not os.path.isdir(materials_dir):
        return False
    parks = load().get('parks', {})
    hit = 0
    for folder in parks:
        if os.path.isdir(os.path.join(materials_dir, folder)):
            hit += 1
            if hit >= 2:
                return True
    return False


def _has_park_subdirs(d):
    """目录下是否直接挂着已知园区文件夹（即它是一份素材总根）。"""
    for folder in load().get('parks', {}):
        if os.path.isdir(os.path.join(d, folder)):
            return True
    return False


def _pick(abs_dir, base, folder, source):
    """在 abs_dir 下定位园区文件夹。

    找不到子目录时：materials_dir 本身就是该园区目录 → 直接用；
    它是一份素材总根（挂着别的园区）→ **返回失败**，绝不退化成扫总根
    （2026-09-11 贵阳事故就是这么来的：脚本写嘉定、目录还没建，
     旧逻辑会回头扫总根，把 5 个园区混在一起）。
    """
    if base == folder:
        return abs_dir, folder, source
    sub = os.path.join(abs_dir, folder)
    if os.path.isdir(sub):
        return sub, folder, source
    if _has_park_subdirs(abs_dir):
        return None, folder, ('园区目录不存在：%s（%s 下挂着其他园区，'
                             '先建好目录再跑）' % (sub, abs_dir))
    return abs_dir, folder, source


def _priority(folder, info):
    """园区命中优先级（表格「命中优先级」列，数字越小越优先，默认 1）。"""
    try:
        return int(info.get('priority') or 1)
    except (TypeError, ValueError):
        return 1


def match_script_parks(script_text, parks):
    """列出脚本正文里命中的所有 (园区, 命中词, 位置)，按判定顺序排好。

    排序规则（2026-09-14 新仓事故后确立）：
      1. 命中的词**越长越优先** —— 长词更具体。「沈海高速新仓出口」里
         "新仓出口" 比 "嘉兴" 具体，应判给新仓，而不是按出现位置判给嘉兴。
      2. 同长度再看「命中优先级」列（数字小优先）。
      3. 仍相同才看出现位置（最早优先）—— 保持旧行为。
    """
    hits = []
    for folder, info in parks.items():
        for kw in script_keywords(folder, info):
            pos = (script_text or '').find(kw)
            if pos >= 0:
                hits.append((folder, kw, pos, _priority(folder, info)))
    hits.sort(key=lambda h: (-len(h[1]), h[3], h[2]))
    return hits


def resolve_park_dir(materials_dir, park=None, script_text=None, park_name=None):
    """确定这条片子该用哪个园区文件夹。

    优先级（2026-09-14 起）：
      1. `--park`（命令行显式指定）
      2. `park_name`（界面/命令行填的园区名 —— 用户意图，过去被只当命名用，已纳入锁定）
      3. 脚本地名词（更具体的词优先，见 match_script_parks）
      4. 素材目录名本身
    返回 (目录, 园区文件夹名, 来源)；识别不到返回 (None, None, 原因)。
    """
    if not materials_dir:
        return None, None, '未提供素材目录'
    rules = load()
    parks = rules.get('parks', {})
    abs_dir = os.path.abspath(materials_dir)
    base = os.path.basename(abs_dir.rstrip('/\\'))

    # 1) 命令行显式指定
    if park:
        for folder, info in parks.items():
            if any(park == n or (n and (park in n or n in park)) for n in _park_names(folder, info)):
                return _pick(abs_dir, base, folder, '--park')

    # 2) 界面/命令行填的园区名（用户意图优先于脚本推断）
    if park_name:
        for folder, info in parks.items():
            if any(park_name == n or (n and (park_name in n or n in park_name))
                   for n in _park_names(folder, info)):
                return _pick(abs_dir, base, folder, '界面园区名')

    # 3) 从脚本正文识别（只认表里 C 列的识别词；更具体的词优先）
    if script_text:
        hits = match_script_parks(script_text, parks)
        if hits:
            return _pick(abs_dir, base, hits[0][0], '脚本地名')

    # 4) 素材目录名本身就是园区文件夹
    if base in parks:
        return abs_dir, base, '目录名'

    return None, None, '未识别到园区（脚本/参数/目录名都没有园区地名）'


def cross_buckets():
    """返回允许跨园区借用的语义桶集合。"""
    rules = load()
    return set(b for b, cfg in (rules.get('buckets') or {}).items()
               if isinstance(cfg, dict) and cfg.get('cross'))


def excluded_parks(bucket):
    """某桶跨园区借用时要排除的园区文件夹名列表。"""
    rules = load()
    cfg = (rules.get('buckets') or {}).get(bucket) or {}
    return [p for p in (cfg.get('exclude') or []) if p]


def nearby_parks(folder):
    """某园区的邻近园区列表（2026-09-14 用户拍板加，人工在表里维护）。

    用途：跨园区借用时**先从邻近园区取**，取完再取远处的 —— 不放开新桶，
    只降低穿帮概率（长三角几家的宿舍楼、办公楼彼此更像）。
    """
    if not folder:
        return []
    info = (load().get('parks') or {}).get(folder) or {}
    return [p for p in (info.get('nearby') or []) if p]
