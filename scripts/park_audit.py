# -*- coding: utf-8 -*-
"""素材来源审计：核对一条成片里的画面是否来自规则允许的园区。

为什么需要：2026-09-11 贵阳片的事故是"画面配错了没人发现"——
7 段素材全部来自新疆/北京/嘉兴，画面本身干净无瑕疵（黑场/冻结/静音
这些信号层 QA 全 PASS），错的只是**来源**。所以审计放在"来源/语义"这一层：

  1. 每段素材落在哪个园区文件夹？（`E:/园区素材/<园区>/...`）
  2. 属于哪个语义桶？（按目录链 + 文件名判定，与选片同源）
  3. 和规则表（config/park_rules.json）核对：
     - 本园区素材 → 通过
     - 兄弟园区素材 → 仅当该桶被标记"可跨园区通用"、且本园区不在该桶的
       "通用例外"里 → 通过（记为"借用"）
     - 其余 → 不通过

预期园区从草稿目录里的 `park_lock.json`（出片时写入）读取；老草稿没有该
文件时回退查产出台账；都没有则**跳过并告警**，绝不默认通过（宁可说
"不确定"，也不给假 PASS）。
"""
import csv
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    import park_rules as _pr
except Exception:
    _pr = None

LOCK_NAME = 'park_lock.json'
PARK_SEP = '/'


def park_of(path, parks):
    """素材路径属于哪个园区文件夹（无则 None）。取最长匹配，避免园区名互相包含。"""
    norm = (path or '').replace('\\', '/')
    best = None
    for folder in parks:
        if PARK_SEP + folder + PARK_SEP in norm:
            if best is None or len(folder) > len(best):
                best = folder
    return best


def park_dir_of(path, folder):
    """从素材路径里切出它所属园区的目录（不依赖 lock，自带定位）。"""
    norm = (path or '').replace('\\', '/')
    idx = norm.rfind(PARK_SEP + folder + PARK_SEP)
    if idx < 0:
        return None
    return norm[:idx + len(folder) + 1]


def bucket_of(path, park_dir):
    """素材的语义桶（与选片同一套判定）。"""
    if not path or not park_dir:
        return None
    try:
        import material_matcher as mm
        rel = os.path.relpath(os.path.dirname(path), park_dir)
        chain = [p for p in rel.replace('\\', '/').split('/') if p and p != '.']
        name = os.path.splitext(os.path.basename(path))[0]
        return mm.classify_with_dir(chain, name)
    except Exception:
        return None


def read_lock(draft_dir):
    """读取出片时写入的园区锁定信息。"""
    p = os.path.join(draft_dir, LOCK_NAME)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) and data.get('park') else None
    except Exception:
        return None


def read_lock_from_log(draft_name, log_path=None):
    """老草稿兜底：从产出台账找同名草稿最近一条，取其园区与素材目录。"""
    if not draft_name:
        return None
    if log_path is None:
        log_path = os.path.join(os.path.dirname(_HERE), 'output', 'production_log.csv')
    if not os.path.isfile(log_path):
        return None
    hit = None
    try:
        with open(log_path, encoding='utf-8-sig', newline='') as f:
            for row in csv.DictReader(f):
                if (row.get('draft_name') or '').strip() == draft_name:
                    hit = row
    except Exception:
        return None
    if not hit:
        return None
    mdir = (hit.get('materials_dir') or '').strip()
    park = os.path.basename(mdir.rstrip('/\\')) if mdir else ''
    if not park:
        return None
    return {'park': park, 'materials_dir': mdir, 'decided_by': '台账', 'rules_version': None}


def audit(paths, lock, rules=None):
    """审计一批素材路径。

    paths: 素材绝对路径列表（按时间线顺序，可重复）
    lock:  {'park': '贵阳园区', 'materials_dir': ..., 'decided_by': ..., 'rules_version': N}
    rules: park_rules.load() 的结果；缺省自行加载

    返回 dict：status / counts / borrowed / problems / rows / locked
    """
    if not lock or not lock.get('park'):
        return {'status': 'SKIP', 'counts': {}, 'borrowed': {}, 'problems': [],
                'rows': [], 'locked': None,
                'reason': '草稿里没有 park_lock.json，台账里也查不到该草稿的园区'}

    rules = rules or (_pr.load() if _pr else {})
    parks = list((rules.get('parks') or {}).keys())
    cross = _pr.cross_buckets() if _pr else set()
    locked = lock['park']

    counts, bor, problems, rows = {}, {}, [], []
    viol = {}
    for path in paths:
        pk = park_of(path, parks)
        b = bucket_of(path, park_dir_of(path, pk)) if pk else None
        key = pk or '(园区外)'
        counts[key] = counts.get(key, 0) + 1

        if pk is None:
            viol[('(园区外)', None, '不在任何园区文件夹下')] = \
                viol.get(('(园区外)', None, '不在任何园区文件夹下'), 0) + 1
            rows.append((pk, b, False, path))
            continue
        if pk == locked:
            rows.append((pk, b, True, path))
            continue
        excluded = _pr.excluded_parks(b) if (_pr and b) else []
        # 通用例外判的是**来源园区**：写"排除 新疆园区"= 不许从新疆借宿舍，
        # 而不是"贵阳不能用宿舍"。2026-09-11 单元测试抓出过这里判反。
        if b and b in cross and pk not in excluded:
            bor[(pk, b)] = bor.get((pk, b), 0) + 1
            rows.append((pk, b, True, path))
        else:
            if not b:
                why = '未识别语义桶'
            elif b not in cross:
                why = '该桶（%s）未开放跨园区' % b
            else:
                why = '该桶的通用例外里排除了这个来源园区（%s）' % pk
            viol[(pk, b, why)] = viol.get((pk, b, why), 0) + 1
            rows.append((pk, b, False, path))

    for (pk, b, why), n in sorted(viol.items(), key=lambda x: -x[1]):
        problems.append('越界素材：%s 的 %s 段 ×%d（%s）' % (pk, b or '未知桶', n, why))

    return {'status': 'FAIL' if problems else 'PASS',
            'counts': counts, 'borrowed': bor, 'problems': problems,
            'rows': rows, 'locked': lock}


def summarize(res):
    """一行摘要，给日志/终端用。"""
    if res['status'] == 'SKIP':
        return '素材来源审计：跳过（%s）' % res.get('reason')
    parts = ['%s %d 段' % (k, v) for k, v in
             sorted(res['counts'].items(), key=lambda x: -x[1])]
    s = '素材来源：%s' % ' / '.join(parts)
    if res['borrowed']:
        br = '；'.join('%s %s %d 段' % (pk, b, n)
                       for (pk, b), n in sorted(res['borrowed'].items()))
        s += '　合规借用：%s' % br
    return s
