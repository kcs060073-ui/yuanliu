# -*- coding: utf-8 -*-
"""草稿命名与草稿目录独占预留 —— 全工程单一实现（P05）。

为什么要单独成一个模块
----------------------
P04 及以前，「拼草稿名」这件事在**两处各写了一遍**：

  * ``scripts/make_park_video.py``（命令行）
  * ``workbench/workbench_gui.py``（``_collect`` / ``_assemble_cmd`` / 草稿名预览）

两边一起算「基础名 + 任务ID尾码」，各自 ``os.makedirs(..., exist_ok=True)``。
后果是真实事故级的：

1. 同一天、同园区、同方向、同风格跑两条 → 基础名完全相同，尾码只有 4 位
   十六进制（65536 分之一），第二次生成会**静默落进同一个目录**，
   用 ``os.replace()`` 把第一条草稿整个覆盖掉。文件锁只保证「不写坏」，
   挡不住「被覆盖」—— 锁是防损坏的，不是防冲突的。
2. GUI 提前把「预期的草稿目录」算出来给自检和弹窗用；一旦目录名因冲突
   发生漂移，GUI 自检的就是**不存在的那个旧目录**，日志与回执也对不上。

本模块把三件事收敛到一处：

* ``base_name()``    —— 生成基础名（0式 / 7式）
* ``sanitize()``     —— 清理 Windows 非法字符
* ``reserve_draft_dir()`` —— **以独占方式预留**唯一的草稿目录

职责边界（与文件锁的分工，别混）
--------------------------------
* **目录预留**（本模块）：负责「同名任务 / 并发任务之间不撞车」。
  靠 ``os.mkdir()`` 的原子性：目录已存在就抛 ``FileExistsError``，
  这是操作系统层面的原子判定，不是「先查再建」的 TOCTOU 竞态。
* **文件锁 + 原子写入**（``atomic_io``）：负责「写到一半被打断 / 文件损坏」。
  两者是两件事，缺一不可，但**谁也不能替代谁**。
"""
import errno
import json
import os
import re
import time

# Windows 文件名禁用字符（外加控制字符）
_ILLEGAL = '<>:"/\\|?*'
_ILLEGAL_RE = re.compile('[%s%s]' % (re.escape(_ILLEGAL),
                                     ''.join(re.escape(chr(c)) for c in range(32))))
# Windows 保留设备名，作为整体名或 <名>.xxx 都不合法
_RESERVED_NAMES = {
    'CON', 'PRN', 'AUX', 'NUL',
    'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
    'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9',
}

# 预留标记文件：写下它的进程 ID / 任务 ID，便于判断「这个目录是我上次留下的」
RESERVE_MARK = '.yuanliu-reserve.json'

# 出现任意一个，就认为该目录里已经是一份「有效草稿」，默认绝对不许覆盖
DRAFT_FILE_MARKERS = ('draft_content.json', 'draft_meta_info.json',
                      'draft_cover.jpg', 'timeline_layout.json',
                      'attachment_pc_common.json')
DRAFT_DIR_MARKERS = ('textReading', 'materials')

MAX_SUFFIX = 99          # 递增后缀上限，超过则换新尾码
TAIL_LEN = 8             # 任务ID随机段（尾码）长度

# 「同一任务中断后重跑可以复用自己预留的目录」——但**必须**能区分
# 「上一次中断留下的」和「此刻正在写的另一路并发」。判据见
# _reservation_resumable()：冷却时间 + 进程归属。
RESERVE_STALE_SECONDS = 90


class DraftDirConflict(RuntimeError):
    """草稿目录已被占用，且不允许覆盖。"""


# ---------------------------------------------------------------------------
# 命名
# ---------------------------------------------------------------------------
def sanitize(name):
    """清理 Windows 非法字符与首尾空白/点；空串返回 '未命名'。

    不做「截断到 260 字符」之类的加工：草稿名短，真超长会在 mkdir 时报错，
    那比悄悄改名更容易发现问题。
    """
    s = _ILLEGAL_RE.sub('_', str(name or '')).strip().strip('.')
    s = re.sub(r'\s+', ' ', s).strip()
    if not s:
        return '未命名'
    if s.upper().split('.')[0] in _RESERVED_NAMES:
        s = '_' + s
    return s


def job_tail(job_id, n=TAIL_LEN):
    """取任务ID 的随机段作为草稿名尾码。

    任务ID 形如 ``YL-260920-093012-DESKTOP58JFBM3-A1B2C3D4``；
    取**最后一段**而不是 ``job_id[-n:]``：
    老任务ID（P04 及以前）随机段只有 4 位，按长度切会把机器码尾巴也切进来。
    """
    jid = str(job_id or '').strip()
    if not jid:
        return ''
    return jid.rsplit('-', 1)[-1].upper()[:max(1, int(n or TAIL_LEN))]


def base_name(naming='0', date_str='', seg='园区', orient_cn='',
              style_name='', editor=''):
    """生成草稿**基础名**（不含任务尾码）。

    * 0式（城市园区）：``YYMMDD-园区[-横/竖]-风格名``
      方向位 2026-09-09 用户拍板加入；不传方向时保持旧格式，兼容既有调用。
    * 7式（实拍园区）：``YYMMDD-园区-横/竖-剪辑者``

    只负责拼字符串，不做「剪辑者必填」这类业务校验 —— 校验放在调用方，
    因为 GUI 抛 ValueError、命令行抛 SystemExit，提示语不同。
    """
    date_str = str(date_str or time.strftime('%y%m%d'))
    seg = str(seg or '园区').strip() or '园区'
    orient_cn = str(orient_cn or '').strip()
    if str(naming) == '7':
        parts = [date_str, seg, orient_cn, str(editor or '').strip()]
    else:
        parts = [date_str, seg]
        if orient_cn:
            parts.append(orient_cn)
        parts.append(str(style_name or '').strip())
    return sanitize('-'.join(p for p in parts if p))


def full_stem(base, job_id='', tail_len=TAIL_LEN):
    """基础名 + 任务尾码。已经带同一尾码时不重复追加（幂等）。

    **只在自动命名时使用**。用户显式 ``--name`` 时绝不能走这里 ——
    那会让「我说的名字」变成「我猜的名字」，覆盖也就打不中目标
    （见 ``reserve_draft_dir(exact=True)``）。
    """
    base = sanitize(base)
    tail = job_tail(job_id, tail_len)
    if not tail:
        return base
    if base.upper().endswith('-' + tail):
        return base
    return sanitize('%s-%s' % (base, tail))


# ---------------------------------------------------------------------------
# 「这是不是一份有效草稿」
# ---------------------------------------------------------------------------
def draft_markers_present(path):
    """返回目录里命中的草稿标志物列表（空列表 = 没有草稿内容）。"""
    if not os.path.isdir(path):
        return []
    hits = [fn for fn in DRAFT_FILE_MARKERS
            if os.path.isfile(os.path.join(path, fn))]
    for dn in DRAFT_DIR_MARKERS:
        d = os.path.join(path, dn)
        if os.path.isdir(d):
            try:
                if os.listdir(d):
                    hits.append(dn + '/')
            except OSError:
                hits.append(dn + '/')
    return hits


def looks_like_draft(path):
    """目录里是否已经存在有效草稿内容。"""
    return bool(draft_markers_present(path))


def read_reservation(path):
    """读目录里的预留标记；没有或损坏返回 {}。"""
    try:
        with open(os.path.join(path, RESERVE_MARK), encoding='utf-8') as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _write_reservation(path, job_id, extra=None):
    data = {'schema_version': 1, 'job_id': job_id or '',
            'reserved_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'pid': os.getpid()}
    if extra:
        data.update(extra)
    try:
        with open(os.path.join(path, RESERVE_MARK), 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except OSError:
        pass
    return data


# ---------------------------------------------------------------------------
# 独占预留
# ---------------------------------------------------------------------------
def _parse_ts(s):
    try:
        return time.mktime(time.strptime(str(s), '%Y-%m-%d %H:%M:%S'))
    except Exception:
        return None


def reservation_resumable(res, now=None):
    """这条预留标记能不能被「同一个任务」复用？

    P05 §1：**不能见标记就复用**。若同一 job_id 有两路写入者（GUI 批量池 /
    双开窗口 / 重试与首发重叠），复用同一目录等于两个人往同一堆文件里写 ——
    这就是「静默覆盖」的翻版，只是从「目录级」下沉到了「文件级」。

    只有同时满足，才算「上一次中断留下、可以接着写」：

    1. 标记是**别的进程**写的（``pid`` ≠ 本进程）。本进程写过就说明可能还在写。
    2. 距预留时间已过 ``RESERVE_STALE_SECONDS`` 冷却期（默认 90s）。
       刚写的标记 = 另一路并发正活着，不复用。
    """
    if not isinstance(res, dict):
        return False
    try:
        pid = int(res.get('pid'))
    except (TypeError, ValueError):
        return False
    if pid == os.getpid():
        return False
    t = _parse_ts(res.get('reserved_at'))
    if t is None:
        return False
    now = time.time() if now is None else now
    return (now - t) >= RESERVE_STALE_SECONDS


def _claim(path, job_id, extra=None):
    """原子地创建并预留目录。成功返回 True；已存在返回 False。"""
    try:
        os.mkdir(path)
    except FileExistsError:
        return False
    except OSError as e:
        if e.errno == errno.EEXIST:
            return False
        raise
    _write_reservation(path, job_id, extra)
    return True


def _candidates(out_root, stem, max_suffix=MAX_SUFFIX, tail=''):
    """候选名字序列：stem → stem-2 → stem-3 … → 换新尾码。"""
    yield os.path.join(out_root, stem), stem, ''
    for i in range(2, int(max_suffix) + 1):
        name = '%s-%d' % (stem, i)
        yield os.path.join(out_root, name), name, 'suffix'
    # 后缀全被占：换一段新尾码再试（尾码由调用方给前缀，这里只加随机段）
    import secrets
    for _ in range(8):
        name = '%s-%s' % (stem, secrets.token_hex(4).upper())
        yield os.path.join(out_root, name), name, 'retail'


def reserve_draft_dir(out_root, base, job_id='', overwrite=False,
                      max_suffix=MAX_SUFFIX, tail_len=TAIL_LEN, extra=None,
                      exact=False):
    """以独占方式预留草稿目录，返回描述字典。

    返回::

        {'path': 真实目录, 'name': 目录名, 'reserved': bool,
         'reused': bool, 'collision': bool, 'reason': str, 'conflict_with': [..]}

    ``exact=True``（用户显式 ``--name``）：
        目标名就是 ``sanitize(base)`` **本身，不追加任务尾码**。
        「显式命名」的语义是"我说到哪个目录就哪个目录"，再叠一层随机尾码
        等于用户说了不算 —— 那正是老 bug：``--name 固定草稿 --overwrite``
        却写进了 ``固定草稿-A1B2C3D4``，同时把真正的 ``固定草稿`` 留着没动。

    ``overwrite=True`` 只在调用方**明确指定了目标名**时才应传入：
        候选目录已存在就直接沿用（这就是「覆盖指定目标」），且**只认第一个候选**
        （准确指定的那个名字），后缀候选一律不允许覆盖。
        默认 ``overwrite=False``：候选目录只要已经存在，一律换新名字重试，
        绝不动已有内容（§2.2）。
    """
    out_root = os.path.abspath(out_root)
    os.makedirs(out_root, exist_ok=True)
    stem = sanitize(base) if exact else full_stem(base, job_id, tail_len)
    conflicts = []
    last = None

    for idx, (path, name, kind) in enumerate(_candidates(out_root, stem, max_suffix)):
        last = {'path': path, 'name': name}
        exists = os.path.isdir(path)
        if not exists:
            if _claim(path, job_id, extra):
                return {'path': path, 'name': name, 'reserved': True, 'reused': False,
                        'collision': bool(conflicts), 'reason': '已独占预留',
                        'conflict_with': conflicts}
            # 极端情况：刚好被别人抢建，继续下一个候选
            conflicts.append(name)
            continue

        # 目录已存在
        if overwrite and idx == 0:
            return {'path': path, 'name': name, 'reserved': False, 'reused': True,
                    'collision': True, 'reason': '用户显式 --overwrite，沿用指定目录',
                    'conflict_with': []}

        if exact:
            # 显式命名且不允许覆盖：**目录存在就不许写进去**（哪怕它是空的、
            # 哪怕是本任务上次留下的）。用户指名道姓要一个名字，我们只能另找
            # 一个也没被占用的名字，绝不能动那个已存在的目录。
            conflicts.append('%s(已存在，显式命名不覆盖)' % name)
            continue

        markers = draft_markers_present(path)
        if markers:
            conflicts.append('%s(已有草稿:%s)' % (name, ','.join(markers[:2])))
            continue

        # 空目录 / 只有预留标记：「上一次中断」留下的可以复用，
        # 但必须过 reservation_resumable() 这一关（区分中断残留 vs 并发在写）。
        res = read_reservation(path)
        if job_id and res.get('job_id') == job_id and reservation_resumable(res):
            return {'path': path, 'name': name, 'reserved': True, 'reused': True,
                    'collision': bool(conflicts),
                    'reason': '同一任务中断后重跑，复用已预留目录',
                    'conflict_with': conflicts}
        if job_id and res.get('job_id') == job_id:
            # 同任务的另一路并发正在写 / 刚写过：换名字，绝不共用目录
            conflicts.append('%s(同任务另一路写入中)' % name)
            continue
        if not os.listdir(path):
            # 完全空的残留目录：不删（§1.9），换个名字走
            conflicts.append('%s(空目录残留)' % name)
            continue
        conflicts.append('%s(非空但无草稿)' % name)

    raise DraftDirConflict(
        '无法为「%s」预留唯一草稿目录：连续 %d 个候选名都已被占用。\n'
        '  %s\n'
        '  请清理草稿根目录里的空目录，或用 --name 指定一个新名字。'
        % (stem, len(conflicts), '；'.join(conflicts[:6])))


def plan_target(out_root, base, job_id='', explicit_name=None,
                overwrite=False, tail_len=TAIL_LEN, extra=None):
    """给调用方用的一步式入口。

    * ``explicit_name`` 非空（用户显式 ``--name``）→ 用**清洗后的准确名字**，
      不追加任务尾码；
      - 带 ``overwrite``：允许沿用那个已存在的目录（覆盖指定目标）。
      - 不带 ``overwrite``：绝不覆盖，同名被占就自动加唯一尾码。
    * ``explicit_name`` 为空 → 自动命名：**永远不允许覆盖**，
      即使调用方误传了 ``overwrite=True`` 也强制忽略
      （工作台即此路径，§2.4「GUI 默认不提供覆盖行为」）。
    """
    if explicit_name:
        return reserve_draft_dir(out_root, explicit_name, job_id=job_id,
                                 overwrite=bool(overwrite), tail_len=tail_len,
                                 extra=extra, exact=True)
    return reserve_draft_dir(out_root, base, job_id=job_id,
                             overwrite=False, tail_len=tail_len, extra=extra)
