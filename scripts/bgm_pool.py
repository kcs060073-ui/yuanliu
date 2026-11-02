# -*- coding: utf-8 -*-
"""BGM 选曲池：只从**本机真实存在**的音频里收集可用曲目。

P05（2026-09-20）改动要点 —— 从「模板缓存路径」改为「本机动态解析」
------------------------------------------------------------------
P04 及以前，`fixed` 模式以及"音乐池为空"时的兜底，都会把**模板草稿里那条
管理员电脑的绝对路径**（``C:/Users/<管理员>/AppData/Local/JianyingPro/User Data/
Cache/music/<md5>.mp3``）原样写进同事的草稿。后果：

* 同事的草稿指向一个**自己机器上根本不存在的文件**；
* 剪映打开后无 BGM 或直接报错，而日志却显示"已选曲"。

现在只做一件事：**只把 os.path.isfile() 为真的本机路径写进草稿**；
一个都找不到时明确返回"本机未找到可用BGM"，由上层生成无 BGM 草稿，
并在 GUI / 日志 / 任务回执 / 完成弹窗里醒目标记 —— 绝不伪装成成功。

候选来源（按优先级）
--------------------
1. 本机草稿库里已被使用过的音乐（``scan_pool``，能读到完整元数据 → 有 resource_id）
2. 剪映音乐缓存 ``User Data/Cache/music`` —— **两个位置都要扫**（见
   ``music_cache_dirs()``）：安装目录反推的根、以及
   ``%LOCALAPPDATA%\\JianyingPro``（剪映装在 D 盘时缓存常常在这个用户目录下）
3. Skill 自带的 ``assets/bgm/``（当前为空：**没有确认可合法分发的公共 BGM
   就不打包任何音乐**，见 SKILL.md）

用法：
    rec, note = bgm_pool.resolve_bgm('random', out_root, install_dir, seed=42,
                                     style_bgm=style.get('bgm', {}), warnings=warn)
    draft, info = draft_builder.build_draft(..., bgm_choice=rec)
"""
import json
import os
import random

import platform_paths as _pp

DEFAULT_DRAFTS_DIR = r'D:/JianyingPro Drafts'

# 需要从草稿音乐素材里带走的字段（喂给 make_bgm_audio_material）
_FIELDS = ('name', 'path', 'duration', 'music_id', 'app_id', 'resource_id',
           'category_name', 'category_id', 'request_id', 'music_source')

# 硬下限：短于此值的音乐不进选曲池（避免极端短音效/试听碎片混入）。
# 2026-09-20 实测本机剪映音乐缓存 54 首：碎片 0.19~3.77s（42 首）、真曲子 67~713s（8 首），
# 中间 4~67s 是空的，所以 15s 落在空档中间，两侧都留足余量。
# ⚠️ 这个下限原本只作用在「草稿库」来源（见 _scan_pool_inproc），
#    「剪映音乐缓存」来源漏了 —— 而缓存里 42/54 都是试听碎片，
#    于是出片 BGM 约 86% 概率选中碎片（被循环铺上百段）。现已在选曲前统一过滤。
MIN_DURATION_US = 15 * 1000 * 1000

# 剪映音乐缓存的相对位置（相对 JianyingPro 根目录）
MUSIC_CACHE_REL = os.path.join('User Data', 'Cache', 'music')
AUDIO_EXTS = ('.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg')

# 子进程 stdout 会被 videoeditor.dll 的版本打印污染（"V bytenn espresso
# version: x.y.z" / "mobilecv2: x.y.z"），DLL 在加载和退出时都会往 stdout 写。
# 因此 JSON 用哨兵行包裹，父进程只取哨兵之间的内容。
BEGIN = '<<<BGM_POOL_JSON>>>'
END = '<<<END_BGM_POOL_JSON>>>'


def scan_pool(drafts_dir=DEFAULT_DRAFTS_DIR, install_dir=''):
    """扫描草稿目录，收集本地缓存仍存在的音乐记录。返回列表（按名称排序）。

    注意：必须以子进程执行——在主进程里加载 videoeditor.dll 会破坏 cv2
    的视频探测，导致后续素材匹配全军覆没（v9 踩过的坑）。
    """
    import subprocess
    import sys
    import tempfile
    if not os.path.isdir(drafts_dir):
        print('[BGM] 草稿目录不存在，跳过扫描：', drafts_dir)
        return []
    # 2026-11-02：剪映 11.7 的 videoeditor.dll 会往 stdout 写版本行，
    # 且可能落在哨兵之间，把 JSON 打坏（实测 "Expecting value: line 1 column 2"
    # → 整个曲库被判为空）。改为子进程把结果写临时文件，stdout 只作兜底。
    fd, out_path = tempfile.mkstemp(prefix='bgm_pool_', suffix='.json')
    os.close(fd)
    cmd = [sys.executable, '-X', 'utf8', os.path.abspath(__file__),
           '--json', os.path.abspath(drafts_dir), install_dir or '',
           '--json-out', out_path]
    pool = []
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=300)
        payload = ''
        try:
            with open(out_path, encoding='utf-8') as f:
                payload = f.read().strip()
        except OSError:
            payload = ''
        if not payload:
            txt = out.stdout.decode('utf-8', errors='replace')
            if BEGIN in txt and END in txt:
                payload = txt.split(BEGIN, 1)[1].split(END, 1)[0].strip()
                # 去掉 DLL 噪声行，只保留从第一个 { 或 [ 开始的内容
                for i, ch in enumerate(payload):
                    if ch in '{[':
                        payload = payload[i:]
                        break
        if not payload:
            err = (out.stderr.decode('utf-8', errors='replace') or
                   out.stdout.decode('utf-8', errors='replace'))[-300:]
            print('[BGM] 子进程扫描失败，返回空池。输出尾部：', err)
            return []
        pool = json.loads(payload)
    except Exception as e:
        print('[BGM] 扫描异常，返回空池：', e)
        return []
    finally:
        try:
            os.remove(out_path)
        except OSError:
            pass
    print('[BGM] 扫描完成，收集到 %d 首本地可用的音乐' % len(pool))
    return pool


def _scan_pool_inproc(drafts_dir, install_dir):
    """真实扫描逻辑（仅在子进程里执行）。"""
    pool = {}
    try:
        from jy_crypto import EncryptUtils
        eu = EncryptUtils(install_dir)
    except Exception as e:
        print('[BGM] 无法初始化剪映加解密，跳过草稿扫描：', e)
        return []
    n_scanned = 0
    for entry in sorted(os.listdir(drafts_dir)):
        f = os.path.join(drafts_dir, entry, 'draft_content.json')
        if not os.path.isfile(f):
            continue
        try:
            raw = open(f, 'rb').read()
            txt = eu.decrypt(raw)
            if isinstance(txt, bytes):
                txt = txt.decode('utf-8')
            j = json.loads(txt)
            n_scanned += 1
        except Exception:
            continue
        for a in j.get('materials', {}).get('audios', []):
            if a.get('type') != 'music':
                continue
            p = a.get('path', '')
            if not p or not os.path.isfile(p):
                continue
            rec = {k: a.get(k, '') for k in _FIELDS}
            rec['duration'] = int(a.get('duration') or 0)
            if rec['duration'] < MIN_DURATION_US:
                continue
            rec['source_draft'] = entry
            pool.setdefault(p, rec)  # 同一首歌多草稿复用时取第一次出现
    return sorted(pool.values(), key=lambda r: r['name'] or '')


def pick_random(pool, seed=None, exclude_paths=()):
    """从池子里随机选一首。seed 传入可复现；exclude_paths 排除指定路径（如上次用的）。"""
    cands = [r for r in pool if r['path'] not in set(exclude_paths)]
    if not cands:
        cands = list(pool)
    if not cands:
        return None
    rng = random.Random(seed) if seed is not None else random.Random()
    return rng.choice(cands)


# ---------------------------------------------------------------------------
# P05：本机可用 BGM 动态解析
# ---------------------------------------------------------------------------
def jianying_root(install_dir=''):
    """由剪映安装目录反推 JianyingPro 根目录（兼容 <root>\\<版本> 与 <root>\\Apps\\<版本>）。"""
    if not install_dir:
        return ''
    d = os.path.abspath(install_dir)
    parent = os.path.dirname(d)
    if os.path.basename(parent).lower() == 'apps':       # <root>\Apps\<版本>
        return os.path.dirname(parent)
    return parent                                        # <root>\<版本>


def _mp3_duration_us(path):
    """纯 Python 估 MP3 时长（Xing/Info 帧头优先，否则按 CBR 码率估算）。

    为什么需要：缓存里翻出来的 mp3 没有草稿元数据，而 BGM 段要靠时长
    循环铺满整条片子 —— 时长估错会变成"后半段没声"。
    """
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            # 先跳过 ID3v2 头：带大封面（几百 KB）的 mp3，第一个音频帧远在
            # 64KB 窗口之外 —— 直接读头部会"找不到同步字节"→ 时长 0 → 曲目被当成
            # 无效丢掉（本机实测 4 首正常 BGM 因此探测失败）。
            first = f.read(10)
            start = 0
            if first[:3] == b'ID3' and len(first) == 10:
                sz = ((first[6] & 0x7f) << 21) | ((first[7] & 0x7f) << 14) | \
                     ((first[8] & 0x7f) << 7) | (first[9] & 0x7f)
                start = 10 + sz
            # 统一回位：无 ID3 时 start=0 也要 seek(0)，否则会从第 10 字节读起、
            # 漏掉"帧头就在文件开头"的普通 mp3（实测会让时长算成 0）。
            f.seek(start)
            head = f.read(64 * 1024)
    except OSError:
        return 0
    i = head.find(b'\xff')
    while 0 <= i < len(head) - 4:
        b1, b2 = head[i + 1], head[i + 2]
        if (b1 & 0xE0) == 0xE0:
            ver_bits = (b1 >> 3) & 0x03          # 3=MPEG1, 2=MPEG2, 0=MPEG2.5
            layer = (b1 >> 1) & 0x03             # 1=Layer3
            br_idx = (b2 >> 4) & 0x0F
            if layer == 1 and ver_bits in (0, 2, 3) and 0 < br_idx < 15:
                rates = {3: [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
                         2: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
                         0: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]}
                kbps = rates[ver_bits][br_idx]
                srates = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000],
                          0: [11025, 12000, 8000]}[ver_bits]
                # 采样率索引 3 是 MPEG 的保留值（非法）——假同步字节常撞到这里。
                # 不判越界会抛 IndexError，而上层是"整目录 try/except"：
                # 一个坏字节就能让整个缓存目录 50+ 首曲子全被跳过（2026-09-20 实测）。
                srate_idx = (b2 >> 2) & 0x03
                if srate_idx >= len(srates):
                    i = head.find(b'\xff', i + 1)
                    continue
                srate = srates[srate_idx]
                if kbps and srate:
                    # Xing/Info 标签里有精确帧数
                    for tag in (b'Xing', b'Info'):
                        j = head.find(tag, i, i + 200)
                        if j > 0:
                            flags = int.from_bytes(head[j + 4:j + 8], 'big')
                            if flags & 0x01:
                                frames = int.from_bytes(head[j + 8:j + 12], 'big')
                                spf = 1152 if ver_bits == 3 else 576
                                if frames:
                                    return int(frames * spf * 1e6 / srate)
                    return int(size * 8 * 1e6 / (kbps * 1000))     # CBR 估算
        i = head.find(b'\xff', i + 1)
    return 0


def probe_audio_duration_us(path):
    """音频时长（微秒）：优先用剪映自带 ffmpeg，其次纯 Python 估 MP3。"""
    try:
        from draft_builder import _ffmpeg_duration      # 懒加载，避免循环依赖
        d = _ffmpeg_duration(path)
        if d:
            return int(d)
    except Exception:
        pass
    if os.path.splitext(path)[1].lower() == '.mp3':
        return _mp3_duration_us(path)
    return 0


def _record_for_file(path):
    """给"从缓存目录直接翻到"的音频文件造一条最小可用记录。"""
    p = os.path.abspath(path)
    return {
        'name': os.path.splitext(os.path.basename(p))[0],
        'path': p,
        'duration': probe_audio_duration_us(p),
        'music_id': '', 'app_id': 1775, 'resource_id': '',
        'category_name': '本机音乐缓存', 'category_id': '0',
        'request_id': '', 'music_source': 'local',
        'source': 'local_cache',
    }


def music_cache_dirs(install_dir=''):
    """**所有**可能的剪映音乐缓存目录（运行时推导，不写死任何机器路径）。

    P05 补漏：剪映装在 D 盘，音乐缓存却**不一定**在安装盘。
    剪映把「程序」和「用户数据」分开：程序可能在 D:\\JianyingPro\\Apps\\<版本>，
    而用户数据（含音乐缓存）通常落在 ``%LOCALAPPDATA%\\JianyingPro``。
    只扫安装目录 → 明明有曲子却报「本机未找到可用BGM」。

    顺序（先安装盘，再用户数据盘；重复路径按规范化绝对路径去重）：
      1. 剪映安装目录反推的根 + ``User Data/Cache/music``
      2. ``%LOCALAPPDATA%\\JianyingPro\\User Data\\Cache\\music``

    全部用 ``expandvars`` 运行时展开 —— 换台机器自动跟着变，
    绝不再把某个人的用户目录写进代码。
    """
    out, seen = [], set()

    def _add(d):
        if not d:
            return
        try:
            p = os.path.abspath(os.path.expandvars(d))
        except Exception:
            return
        key = os.path.normcase(p)
        if key in seen:
            return
        seen.add(key)
        out.append(p)

    root = jianying_root(install_dir)
    if root:
        _add(os.path.join(root, MUSIC_CACHE_REL))
    # 用户数据根走平台抽象：Windows=%LOCALAPPDATA%\JianyingPro（与历史一致）、
    # macOS=~/Movies/JianyingPro。Windows 上仍只有一条候选，行为不变。
    for ud_root in _pp.jianying_user_data_roots():
        _add(os.path.join(ud_root, MUSIC_CACHE_REL))
    return out


def collect_local_bgm(out_root='', install_dir='', skill_dir=''):
    """收集**本机真实存在**的 BGM 候选。返回列表（已按路径去重、校验 isfile）。

    任何一步失败都只是少一个来源，不抛异常 —— 出片不该因为"找 BGM"挂掉。
    """
    seen, out = set(), []

    def _add(rec):
        p = (rec or {}).get('path')
        if not p or not os.path.isfile(p):
            return                                  # 硬约束：只接受真实存在的文件
        key = os.path.normcase(os.path.abspath(p))
        if key in seen:
            return
        seen.add(key)
        rec = dict(rec)
        rec['path'] = os.path.abspath(p)
        if not rec.get('duration'):
            rec['duration'] = probe_audio_duration_us(rec['path'])
        out.append(rec)

    # 1) 草稿库里用过的音乐（带完整元数据；子进程扫描，失败即跳过）
    try:
        for rec in scan_pool(out_root or DEFAULT_DRAFTS_DIR, install_dir):
            _add(rec)
    except Exception as e:
        print('[BGM] 草稿库扫描跳过：%s' % e)

    # 2) 剪映音乐缓存目录（**全部**候选位置，不依赖加解密，纯文件系统）
    #    目录不存在/不可读 → 只是少一个来源，绝不报错、绝不中断。
    for cache in music_cache_dirs(install_dir):
        try:
            if not os.path.isdir(cache):
                continue
            got = 0
            for fn in sorted(os.listdir(cache)):
                if os.path.splitext(fn)[1].lower() in AUDIO_EXTS:
                    # 单文件失败不该毁掉整个目录（另一个来源兜底即可）
                    try:
                        before = len(out)
                        _add(_record_for_file(os.path.join(cache, fn)))
                        got += (len(out) - before)
                    except Exception as e:
                        print('[BGM] 跳过 %s：%s' % (fn, e))
            print('[BGM] 音乐缓存 %s：新增 %d 首（累计候选 %d 首）' % (cache, got, len(out)))
        except Exception as e:
            print('[BGM] 音乐缓存扫描跳过（%s）：%s' % (cache, e))

    # 3) Skill 自带公共 BGM（当前未打包任何音乐；有 assets/bgm 时自动生效）
    try:
        here = skill_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        assets = os.path.join(here, 'assets', 'bgm')
        if os.path.isdir(assets):
            for fn in sorted(os.listdir(assets)):
                if os.path.splitext(fn)[1].lower() in AUDIO_EXTS:
                    _add(_record_for_file(os.path.join(assets, fn)))
    except Exception:
        pass

    return sorted(out, key=lambda r: (r.get('name') or '').lower())


def _norm_key(s):
    """匹配用的归一化键：小写、去掉空白与常见分隔符。"""
    import re as _re
    return _re.sub(r'[\s\-_（）()【】\[\]、,，.。·]+', '', str(s or '')).lower()


def match_style_bgm(cands, style_bgm):
    """按风格配置在候选里找默认曲。返回 ``(记录 | None, 命中方式)``。

    匹配优先级（P05 §1.2 明确要求）：
      1. ``resource_id`` **精确**匹配 —— 剪映给每首曲子的稳定 ID，最可靠
      2. 配置里的**名字**精确匹配（候选记录 ``name``）
      3. 配置里的**文件名**匹配（候选的 basename / 去扩展名名）
      4. 名字归一化后匹配（忽略空格、横杠、括号、中英标点）

    为什么不能只看 ``resource_id``：从**剪映音乐缓存**翻出来的 mp3 没有元数据
    （``resource_id`` 为空，名字就是那串 md5），所以按 ID 永远匹配不上；
    而同事的机器上往往只有缓存、没有草稿库。这时按文件名/名字匹配是唯一机会。
    """
    sb = style_bgm if isinstance(style_bgm, dict) else {}
    rid = str(sb.get('resource_id') or '').strip()
    nm = str(sb.get('name') or '').strip()

    if rid:
        for c in cands:
            if str(c.get('resource_id') or '').strip() == rid:
                return c, 'resource_id 精确匹配'
    if nm:
        for c in cands:                                  # 记录名
            if str(c.get('name') or '').strip() == nm:
                return c, '名称精确匹配'
        for c in cands:                                  # 文件名
            base = os.path.basename(str(c.get('path') or ''))
            if base == nm or os.path.splitext(base)[0] == nm:
                return c, '文件名精确匹配'
        key = _norm_key(nm)
        if key:
            for c in cands:
                base = os.path.basename(str(c.get('path') or ''))
                if _norm_key(c.get('name')) == key or _norm_key(base) == key \
                        or _norm_key(os.path.splitext(base)[0]) == key:
                    return c, '名称归一化匹配'
    return None, ''


def resolve_bgm(mode='random', out_root='', install_dir='', seed=None, style_bgm=None,
                skill_dir='', warnings=None):
    """解析本次该用哪首 BGM。返回 ``(record | None, 说明文字)``。

    * ``mode='random'``：从本机可用曲目里随机抽（seed 可复现）
    * ``mode='fixed'`` ：按 ``style_bgm``（来自 ``default_styles.json`` 的
      ``styles[].bgm``）在本机候选里匹配默认曲；匹配不到就**安全降级**到
      本机可用曲目，并把这件事记进 ``warnings`` —— 绝不因为 BGM 缺失让出片失败。
    * 找不到任何可用曲目 → ``(None, '...本机未找到可用BGM...')``，
      调用方据此生成无 BGM 草稿并醒目标记。

    ``warnings``：可选列表；发生"降级"这类**不致命但必须让人看见**的情况时，
    往里追加一条中文说明（GUI / 日志 / 任务回执同源引用）。
    """
    def _warn(msg):
        if warnings is not None:
            warnings.append(msg)
        return msg

    cands = collect_local_bgm(out_root, install_dir, skill_dir)
    # 时长硬下限（2026-09-20 补）：收集层只保证"文件真实存在"（契约不变），
    # 但剪映音乐缓存里混着大量**试听片段/音效**（实测本机 54 首里 42 首仅 0.19~3.77s），
    # 拿它铺满全片 = 同一秒循环上百次，声音完全不能听。这里在**选曲前**剔除。
    # 判定与草稿库来源（_scan_pool_inproc 的 `duration < MIN_DURATION_US`）保持一致：
    # duration 为 0（探测不出）同样剔除 —— 时长未知的音频不该拿去铺满全片。
    if cands:
        keep = [c for c in cands
                if (c.get('duration') or 0) >= MIN_DURATION_US]
        if len(keep) != len(cands):
            dropped = len(cands) - len(keep)
            print('[BGM] 剔除 %d 首过短/时长不明的音频（<%gs，多为试听片段）'
                  % (dropped, MIN_DURATION_US / 1e6))
        cands = keep
    if not cands:
        dirs = music_cache_dirs(install_dir)
        return None, ('本机未找到可用BGM —— 草稿库(%s)、剪映音乐缓存(%s) 与 Skill 自带 '
                      'assets/bgm 里都没有可用音频文件'
                      % (out_root or DEFAULT_DRAFTS_DIR,
                         '、'.join(dirs) if dirs else '未探测到剪映'))

    if str(mode).lower() == 'fixed':
        sb = style_bgm if isinstance(style_bgm, dict) else {}
        rec, how = match_style_bgm(cands, sb)
        if rec:
            return rec, '固定模式：本机命中风格默认曲（%s）' % how
        want = str(sb.get('name') or sb.get('resource_id') or '').strip() or '(风格配置未指定)'
        note = ('固定模式：本机没有风格默认曲「%s」→ 已降级为本机可用曲目（共 %d 首）；'
                '不影响出片' % (want, len(cands)))
        return cands[0], _warn(note)
    rec = pick_random(cands, seed=seed)
    return rec, '随机模式：本机可用曲目 %d 首' % len(cands)


def summarize(rec):
    """人类可读的一行描述。"""
    if not rec:
        # 没有"回退风格默认曲"这回事了：找不到就是不生成 BGM 轨（不伪装成功）
        return '(无 BGM)'
    return '%s | %.0fs | %s' % (
        rec.get('name') or '未命名',
        (rec.get('duration') or 0) / 1e6,
        os.path.basename(rec.get('path') or ''))


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == '--json':
        # 子进程模式：输出 JSON（供 scan_pool 解析）
        dd = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_DRAFTS_DIR
        ii = sys.argv[3] if len(sys.argv) > 3 else ''
        out_path = ''
        if '--json-out' in sys.argv:
            k = sys.argv.index('--json-out')
            out_path = sys.argv[k + 1] if len(sys.argv) > k + 1 else ''
        data = _scan_pool_inproc(dd, ii)
        if out_path:
            # 主通道：落文件，绕开 videoeditor.dll 往 stdout 写噪声
            try:
                with open(out_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False)
            except OSError as e:
                sys.stderr.write('写入 json-out 失败：%s\n' % e)
        print(BEGIN)
        print(json.dumps(data, ensure_ascii=False))
        print(END, flush=True)
        sys.exit(0)
    # 调试模式：人类可读输出
    dd = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DRAFTS_DIR
    ii = sys.argv[2] if len(sys.argv) > 2 else ''
    pool = _scan_pool_inproc(dd, ii)
    for r in pool:
        print(' ', summarize(r))
    print('随机抽一首：', summarize(pick_random(pool)))
