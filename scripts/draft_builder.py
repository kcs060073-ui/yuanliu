# -*- coding: utf-8 -*-
"""草稿生成器：脚本 + 风格 + 素材目录 → 剪映明文草稿 JSON。

以三份已解密模板草稿（templates/style{N}.json）为基底，剥离旧内容后
按选定风格重建核心轨道：

  video 轨道        素材匹配结果（横版铺满，clip.scale 按画布与素材尺寸计算）
  text 字幕轨道     字幕切分结果（sub_type=4 字幕素材，tts_auto_update=true）
  audio TTS 轨道    text_to_audio 素材，tts_auto_update=true → 剪映打开时自动补生成配音
  audio BGM 轨道    风格 BGM（复用模板缓存 mp3 路径）
  text 免责花字轨道 常驻全片

素材路径约定：
  用户素材 → 原路径引用（与模板引用 Cloud files 一致）；若 copy_materials=True，
  make_park_video 会把素材复制进草稿目录 materials/ 并用绝对路径引用。
  TTS wav → ##_draftpath_placeholder_<uuid>_##/textReading/xxx.wav（占位，剪映自动合成）。

用法（库）：
  from draft_builder import build_draft
  draft = build_draft(script, style_key='3', materials_dir='D:/materials',
                      tone_cfg=None, draft_name='xx-风格', copy_materials_to=None)
"""
import json
import os
import re
import uuid
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import subtitle_split
import material_matcher
import jianying_path
import platform_paths as _pp

# 三份模板与 default_styles.json 的路径
_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.path.dirname(_HERE)
CONFIG_DIR = os.path.join(_SKILL_DIR, 'config')
TEMPLATES_DIR = os.path.join(_SKILL_DIR, 'templates')

STYLE_TEMPLATE = {
    '1': os.path.join(TEMPLATES_DIR, 'style1_jiaxing.json'),
    '2': os.path.join(TEMPLATES_DIR, 'style2_jiaxing_zs.json'),
    '3': os.path.join(TEMPLATES_DIR, 'style3_nanjing.json'),
}

# P05 §7：默认字体**不再写死盘符与剪映版本号**。
# 老常量是"某个盘符 + 某个剪映版本号 + Resources/Font/SystemFont/zh-hans.ttf"
# 的完整绝对路径；同事的剪映装在别的盘、或升了版本就失效，而草稿会带着一个
# 不存在的路径静默退回默认字体。现在改为运行时探测
# （剪映安装目录 → Windows 中文字体 → 空串）。
DEFAULT_FONT_PATH = ''      # 兼容旧引用：保留名字，值改为空 = "运行时解析"

# 平台信息里属于**个人设备标识**的字段：绝不带进同事的草稿（P05 §6）。
_DEVICE_ID_KEYS = ('device_id', 'hard_disk_id', 'mac_address')


def resolve_font_path(install_dir='', hint=''):
    """运行时解析一个真实存在的可用中文字体；找不到返回 ''（剪映安全回退）。"""
    try:
        return jianying_path.find_font(install_dir=install_dir, hint=hint)
    except Exception:
        return ''


def sanitize_platform(plat):
    """把模板里的 ``platform`` / ``last_modified_platform`` 洗成非个人化的。

    P05 §6：模板是从管理员的剪映里导出的，``platform`` 里带着
    ``device_id`` / ``hard_disk_id`` / ``mac_address``（管理员机器的硬件标识）。
    这些字段对剪映识别草稿没有作用（云端同步关闭时纯属信息），
    但会把管理员的设备唯一标识扩散到 15 台机器的每一条草稿里。

    处理：**直接删掉**这三个设备标识键（不是置空）—— 置空仍会在草稿 JSON
    里留下 ``"device_id": ""`` 字样，全包隐私搜索不为 0。保留通用字段
    （os / app_id / app_version / app_source / os_version）；``os_version``
    缺失时用本机操作系统版本补齐（通用信息，不是个人标识）。
    """
    if not isinstance(plat, dict):
        return plat
    out = {}
    for k, v in plat.items():
        if k in _DEVICE_ID_KEYS:
            continue
        out[k] = v
    out.setdefault('os', 'windows')
    out.setdefault('app_source', 'lv')
    if not out.get('os_version'):
        try:
            import platform as _pf
            out['os_version'] = _pf.version() or ''
        except Exception:
            out['os_version'] = ''
    return out

# ---------- 素材尺寸探测（mp4/mov/jpg/png） ----------

def _read_boxes(data, start, target, depth=0):
    """递归遍历 MP4/MOV box 树，产出名为 target 的完整 box bytes。"""
    offset = start
    n = len(data)
    while offset + 8 <= n:
        size = int.from_bytes(data[offset:offset + 4], 'big')
        name = data[offset + 4:offset + 8].decode('latin1', errors='replace')
        if size == 1 and offset + 16 <= n:
            size = int.from_bytes(data[offset + 8:offset + 16], 'big')
            hdr = 16
        elif size == 0:
            size = n - offset
            hdr = 8
        else:
            hdr = 8
        if size < hdr or offset + size > n:
            break
        if name == target:
            yield data[offset:offset + size]
        if name in ('moov', 'trak', 'mdia', 'minf', 'stbl') and depth < 8:
            yield from _read_boxes(data, offset + hdr, target, depth + 1)
        offset += size


def _tkhd_base(ver):
    """tkhd 中 width/height 的字节偏移。

    v1: version/flags 之后 creation/modification/duration 各 8 字节 → 96
    v0: 三者各 4 字节 → 84
    ⚠️ 2026-11-02 修正：v0 原先写 76（比正确值少 8 字节），
    读出来是 (0, 16384) 这类垃圾值，导致所有 v0 视频尺寸探测失效、
    竖版视频被迫按 1920x1080 兜底缩放（竖屏素材被放大 1.78 倍、裁切过头）。
    """
    return 96 if ver == 1 else 84


def _tkhd_size(box):
    ver = box[8] if len(box) > 8 else 0
    base = _tkhd_base(ver)
    if len(box) < base + 8:
        return None
    w = int.from_bytes(box[base:base + 4], 'big') / 65536
    h = int.from_bytes(box[base + 4:base + 8], 'big') / 65536
    if w <= 0 or h <= 0:
        return None
    return (int(w), int(h))


def _tkhd_rotation(box):
    """从 tkhd 的 3x3 变换矩阵取旋转角（度，0/90/180/270）。

    手机竖拍常见「编码 1920x1080 + 矩阵旋转 90°」，只看宽高会判成横屏。
    """
    ver = box[8] if len(box) > 8 else 0
    moff = 60 if ver == 1 else 48
    if len(box) < moff + 36:
        return 0

    def fx(i):
        o = moff + i * 4
        return int.from_bytes(box[o:o + 4], 'big', signed=True) / 65536.0

    a, b = fx(0), fx(1)
    if not a and not b:
        return 0
    import math
    deg = math.degrees(math.atan2(b, a))
    return int(round(deg)) % 360


def _probe_image(path):
    """读图片宽高。JPEG 允许前面有非标准头（用户库里多数文件带 ~4KB 前缀，
    纯按文件头签名会读不到），所以在头 1MB 里找 SOI 再解析。"""
    try:
        with open(path, 'rb') as f:
            head = f.read(1024 * 1024)
    except OSError:
        return None
    png = head.find(b'\x89PNG\r\n\x1a\n')
    if png >= 0:
        w = int.from_bytes(head[png + 16:png + 20], 'big')
        h = int.from_bytes(head[png + 20:png + 24], 'big')
        return (w, h) if w > 0 else None
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        # VP8X / VP8L / VP8 三种子格式的宽高位置各不相同
        fmt = head[12:16]
        try:
            if fmt == b'VP8X':
                w = int.from_bytes(head[24:27], 'little') + 1
                h = int.from_bytes(head[27:30], 'little') + 1
                return (w, h)
            if fmt == b'VP8 ':
                i = head.find(b'\x9d\x01\x2a', 12)
                if i > 0:
                    w = int.from_bytes(head[i + 3:i + 5], 'little') & 0x3FFF
                    h = int.from_bytes(head[i + 5:i + 7], 'little') & 0x3FFF
                    return (w, h)
            if fmt == b'VP8L':
                bits = int.from_bytes(head[21:25], 'little')
                return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
        except Exception:
            return None
        return None
    soi = head.find(b'\xff\xd8')
    if soi >= 0:
        i = soi + 2
        while i + 9 < len(head):
            if head[i] != 0xFF:
                i += 1
                continue
            marker = head[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                          0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h = int.from_bytes(head[i + 5:i + 7], 'big')
                w = int.from_bytes(head[i + 7:i + 9], 'big')
                return (w, h) if w > 0 else None
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seg_len = int.from_bytes(head[i + 2:i + 4], 'big')
            if seg_len < 2:
                i += 2
                continue
            i += 2 + seg_len
    return None


def _ffmpeg_image_size(path):
    """兜底：用剪映自带 ffmpeg 读图片尺寸（WebP/带前缀头的 JPEG 都能读）。"""
    ff = _find_ffmpeg()
    if not ff:
        return None
    try:
        import re as _re
        import subprocess
        si = None
        if os.name == 'nt':
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        r = subprocess.run([ff, '-hide_banner', '-i', path], capture_output=True,
                           startupinfo=si, timeout=60)
        text = (r.stderr or b'').decode('utf-8', 'replace')
        m = _re.search(r'Video:.*?(\d{2,5})x(\d{2,5})', text)
        if m:
            return (int(m.group(1)), int(m.group(2)))
    except Exception:
        return None
    return None


def _mvhd_duration(box):
    if len(box) < 32:
        return None
    ver = box[8]
    if ver == 1:
        timescale = int.from_bytes(box[28:32], 'big')
        duration = int.from_bytes(box[32:40], 'big')
    else:
        timescale = int.from_bytes(box[20:24], 'big')
        duration = int.from_bytes(box[24:28], 'big')
    if timescale <= 0:
        return None
    return int(duration * 1_000_000 / timescale)  # 微秒


_DUR_CACHE = {}

# 缓存键版本：改了探测算法就 +1，旧缓存条目自动失效（不然会把历史错值一直读回来）
# v2（2026-11-02）：修正 tkhd v0 偏移 76→84、补尾部 moov 兜底
_SIZE_CACHE_VER = 'v2'
_ORIENT_CACHE_VER = 'o1'


def _find_ffmpeg():
    r"""探测剪映自带的 ffmpeg（edge-tts 模块移除后内联实现）。

    剪映安装目录下通常有 ffmpeg（Windows 上叫 ffmpeg.exe），按版本号倒序取最新。
    目录布局探测统一走 jianying_path（兼容 <版本>\ 与 Apps\<版本>\）；
    文件名走 platform_paths.ffmpeg_name()，macOS 上是无扩展名的 ffmpeg。
    """
    return jianying_path.find_file_in_install(_pp.ffmpeg_name())


def _ffmpeg_duration(path):
    """用剪映自带的 ffmpeg 探测时长，解析 stderr 里的 Duration。"""
    ff = _find_ffmpeg()
    if not ff:
        return None
    try:
        import subprocess
        si = None
        if os.name == 'nt':
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        r = subprocess.run([ff, '-i', path], capture_output=True,
                           startupinfo=si, timeout=120)
        for line in r.stderr.decode('utf-8', 'replace').splitlines():
            if 'Duration:' in line:
                hms = line.split('Duration:')[1].split(',')[0].strip()
                hh, mm, ss = hms.split(':')
                return int((int(hh) * 3600 + int(mm) * 60 + float(ss)) * 1e6)
    except Exception:
        return None
    return None


def probe_media_duration(path):
    """返回素材原始时长（微秒）；探测失败返回 None。

    性能策略（2026-09-04 实测 548 素材库校准）：
    - 旧顺序「ffmpeg 优先」每个文件 spawn 一次子进程 ≈0.3s+，500+ 素材要数分钟，
      且 DJI 新机型文件(ftyp+free+mdat→moov 在尾 ~95%)命中靠尾部窗口，被 ffmpeg 拖死。
    - 新顺序：**纯 mp4 box 解析优先**（头 1MB → 尾 2MB，DJI 尾置 moov 由尾窗命中），
      实测 548 文件 ≈7s；ffmpeg 只做最后兜底（mov/mkv 编码不可靠时）。
    结果按 path+mtime 缓存，避免重复探测。
    """
    if not path or not os.path.isfile(path):
        return None
    ext = os.path.splitext(path)[1].lower()
    if ext not in ('.mp4', '.mov', '.m4v', '.mkv', '.avi'):
        return None
    try:
        key = (os.path.abspath(path), int(os.path.getmtime(path)))
    except OSError:
        key = None
    if key and key in _DUR_CACHE:
        return _DUR_CACHE[key]

    dur = None
    # 快路径 1: moov 前置型 —— 头部窗口覆盖 mvhd（mvhd 是 moov 首个子 box，
    # 只需窗口到 moov 起点即可命中，不必覆盖整个 moov）
    try:
        with open(path, 'rb') as f:
            data = f.read(1024 * 1024)
        for box in _read_boxes(data, 0, 'mvhd'):
            dur = _mvhd_duration(box)
            if dur:
                break
    except OSError:
        pass
    if not dur:
        # 快路径 2: 流式/DJI 机型 moov 在文件尾部（mdat 巨大占前 ~95%），
        # 读最后 2MB 找 mvhd。实测 DJI_2026*_D.MP4 的 mvhd 落在尾 2MB 内，
        # 512KB 窗口会 miss（文件尾还有 ~1MB 其它 box）
        try:
            size = os.path.getsize(path)
            with open(path, 'rb') as f:
                f.seek(max(0, size - 2 * 1024 * 1024))
                tail = f.read()
            idx = tail.find(b'mvhd')
            if idx > 4:
                dur = _mvhd_duration(tail[idx - 4:])
        except OSError:
            pass
    if not dur:
        # 兜底: ffmpeg（对 mov/mkv 等非 mp4 box 结构可靠）
        dur = _ffmpeg_duration(path)
    if key:
        _DUR_CACHE[key] = dur
    return dur


# 尺寸磁盘缓存：445 张图里 118 张要走 ffmpeg 兜底（250ms/张），不缓存的话每次出片
# 都要多等 ~30s。键 = 绝对路径|mtime|size，素材换了会自动失效。
#
# P05：路径改为**按运行模式解析**（runtime_context.output_root()）。
# 老写法把 `<skill>/output/media_size_cache.json` 写死，于是
# `YUANLIU_RUN_MODE=test` 的隔离在这里被绕过 —— 测试跑一次就往生产
# output/ 写一次缓存（2026-09-20 做连续出片 E2E 时实测发现）。
def _size_cache_path():
    import runtime_context
    return os.path.join(runtime_context.output_root(), 'media_size_cache.json')


_SIZE_CACHE = None
_SIZE_NEW = [0]


def _size_cache():
    global _SIZE_CACHE
    if _SIZE_CACHE is None:
        try:
            with open(_size_cache_path(), encoding='utf-8') as f:
                data = json.load(f)
            _SIZE_CACHE = data if isinstance(data, dict) else {}
        except Exception:
            _SIZE_CACHE = {}
    return _SIZE_CACHE


def _size_cache_save(force=False):
    # 缓存没被加载过就保存 = 会把文件写成 null（2026-09-15 踩过：图片全被判成探测失败）
    if _SIZE_CACHE is None:
        return
    if not force and _SIZE_NEW[0] < 200:
        return
    try:
        path = _size_cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(_SIZE_CACHE, f)
        _SIZE_NEW[0] = 0
    except Exception:
        pass


import atexit as _atexit
_atexit.register(lambda: _size_cache_save(force=True))


def probe_media_size(path):
    """返回 (width, height)；探测失败返回 None。（带磁盘缓存，见 _SIZE_CACHE_PATH）"""
    if not path or not os.path.isfile(path):
        return None
    cache = _size_cache()
    key = None
    try:
        key = '%s|%s|%d|%d' % (_SIZE_CACHE_VER, os.path.abspath(path),
                               int(os.path.getmtime(path)), os.path.getsize(path))
        if key in cache:
            v = cache[key]
            return tuple(v) if v else None
    except OSError:
        key = None
    wh = _probe_media_size_uncached(path)
    if key:
        cache[key] = list(wh) if wh else []
        _SIZE_NEW[0] += 1
        _size_cache_save()
    return wh


def _tkhd_boxes_in(path):
    """产出文件的 tkhd box（bytes）。

    先按 box 树解析头部 2MB（moov 前置型）；DJI 等机型 moov 在文件尾部
    （mdat 占据前 ~95%），头窗必然 miss，此时按 `tkhd` 关键字在尾 2MB 兜底定位。
    2026-11-02 新增：此前只读头 2MB，实测 DJI 素材尺寸全部探测失败。
    """
    try:
        with open(path, 'rb') as f:
            head = f.read(2 * 1024 * 1024)
    except OSError:
        return
    found = False
    try:
        for box in _read_boxes(head, 0, 'tkhd'):
            found = True
            yield box
    except Exception:
        pass
    if found or len(head) < 2 * 1024 * 1024:
        return
    # 尾部兜底：找 b'tkhd'，回退 4 字节拿到 size
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            f.seek(max(0, size - 2 * 1024 * 1024))
            tail = f.read()
    except OSError:
        return
    i = 0
    while True:
        i = tail.find(b'tkhd', i)
        if i < 4:
            break
        start = i - 4
        bsz = int.from_bytes(tail[start:start + 4], 'big')
        if 8 <= bsz <= len(tail) - start:
            yield tail[start:start + bsz]
        i += 4


def _probe_media_size_uncached(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.jpg', '.jpeg', '.png', '.bmp', '.webp', '.jfif'):
        wh = _probe_image(path)
        return wh or _ffmpeg_image_size(path)      # 带非标准头的图靠 ffmpeg 兜底
    if ext in ('.mp4', '.mov', '.m4v'):
        for box in _tkhd_boxes_in(path):
            wh = _tkhd_size(box)
            if wh:
                return wh
    return None


# ---------- 真实画面朝向（像素尺寸 + 旋转矩阵） ----------

_ORIENT_CACHE = None
_ORIENT_NEW = [0]


def _orient_cache_path():
    import runtime_context
    return os.path.join(runtime_context.output_root(), 'media_orientation_cache.json')


def _orient_cache():
    global _ORIENT_CACHE
    if _ORIENT_CACHE is None:
        try:
            with open(_orient_cache_path(), encoding='utf-8') as f:
                data = json.load(f)
            _ORIENT_CACHE = data if isinstance(data, dict) else {}
        except Exception:
            _ORIENT_CACHE = {}
    return _ORIENT_CACHE


def _orient_cache_save(force=False):
    if _ORIENT_CACHE is None:
        return
    if not force and _ORIENT_NEW[0] < 200:
        return
    try:
        path = _orient_cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(_ORIENT_CACHE, f)
        _ORIENT_NEW[0] = 0
    except Exception:
        pass


_atexit.register(lambda: _orient_cache_save(force=True))


def _probe_orientation_uncached(path):
    """返回 'portrait'/'landscape'；探测失败返回 None。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.mp4', '.mov', '.m4v'):
        for box in _tkhd_boxes_in(path):
            wh = _tkhd_size(box)
            if not wh:
                continue
            w, h = wh
            if _tkhd_rotation(box) in (90, 270):
                w, h = h, w
            return 'landscape' if w > h else ('portrait' if h > w else None)
        return None
    wh = probe_media_size(path)          # 图片：exif 旋转不处理，按像素宽高判
    if not wh or wh[0] == wh[1]:
        return None
    return 'landscape' if wh[0] > wh[1] else 'portrait'


def probe_media_orientation(path):
    """素材真实画面朝向（旋转感知）；探测失败返回 None。

    与文件名/目录名标注无关 —— 标注只表达人的意图，像素才决定放得进哪种画布。
    结果按 path+mtime+size 缓存到 output/media_orientation_cache.json。
    """
    if not path or not os.path.isfile(path):
        return None
    cache = _orient_cache()
    key = None
    try:
        key = '%s|%s|%d|%d' % (_ORIENT_CACHE_VER, os.path.abspath(path),
                               int(os.path.getmtime(path)), os.path.getsize(path))
        if key in cache:
            v = cache[key]
            return v or None
    except OSError:
        key = None
    o = _probe_orientation_uncached(path)
    if key:
        cache[key] = o or ''
        _ORIENT_NEW[0] += 1
        _orient_cache_save()
    return o


def probe_media_display_size(path):
    """显示尺寸（已应用旋转）；探测失败返回 probe_media_size 的结果或 None。

    竖拍素材编码常是 1920x1080 + 矩阵旋转 90°，真实显示是 1080x1920。
    画布 cover 缩放必须按显示尺寸算，否则竖屏素材会被放大 1.78 倍、裁切过头
    （2026-11-02 修正；此前一律按编码尺寸或 1920x1080 兜底）。
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.mp4', '.mov', '.m4v'):
        for box in _tkhd_boxes_in(path):
            wh = _tkhd_size(box)
            if wh:
                w, h = wh
                if _tkhd_rotation(box) in (90, 270):
                    w, h = h, w
                return (w, h)
    return probe_media_size(path)


# ---------- 风格 / 音色 ----------

def _load_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def resolve_style(style_key, styles=None):
    """把用户输入（数字或中文名）解析为 styles 字典的 key（'1'/'2'/'3'）。"""
    if styles is None:
        styles = _load_json(os.path.join(CONFIG_DIR, 'default_styles.json'))['styles']
    for k, cfg in styles.items():
        if str(style_key) == str(k) or str(style_key) in cfg.get('aliases', []):
            return k, cfg
    raise ValueError(f'未识别的风格：{style_key}，可选 1三连否 / 2招商 / 3没想到')


def pick_tone(style_key, tone_cfg=None, pool=None, style_name=''):
    """选定 TTS 音色。tone_cfg 传参则直接使用；否则从音色池随机抽取。
    随机策略：优先取与当前风格匹配的 confirmed 音色，其次全局 confirmed。
    """
    if tone_cfg and tone_cfg.get('tone_speaker'):
        return tone_cfg
    if pool is None:
        pool = _load_json(os.path.join(CONFIG_DIR, 'tone_pool.json'))
    confirmed = pool.get('confirmed', [])
    if not confirmed:
        return {}
    if style_name:
        matched = [t for t in confirmed if style_name in t.get('style', [])]
    else:
        matched = []
    source = matched or confirmed
    chosen = random.choice(source)
    return {
        'tone_type': chosen.get('tone_type', ''),
        'tone_speaker': chosen.get('tone_speaker', ''),
        'resource_id': chosen.get('resource_id', ''),
        'tone_effect_name': chosen.get('tone_type', ''),
        'tone_platform': chosen.get('tone_platform', 'sami'),
        'tone_emotion_name_key': 'voice_style_default',
        'gender': chosen.get('gender', ''),
    }


# ---------- 段 / 素材工厂 ----------

def _uuid():
    return str(uuid.uuid4()).upper()


# ---------- 辅助素材（剪映要求每个段引用，缺失会导致 TTS/字幕/视频渲染异常） ----------

def make_speed_material(speed=None):
    m = {'id': _uuid(), 'type': 'speed'}
    if speed is not None:
        m['speed'] = speed
    return m


def make_placeholder_info_material():
    return {'id': _uuid(), 'type': 'placeholder_info', 'meta_type': 'none'}


def make_beats_material():
    return {'id': _uuid(), 'type': 'beats', 'ai_beats': {'melody_percents': [0.0]}}


def make_sound_channel_mapping_material():
    return {'id': _uuid(), 'type': 'none'}


def make_vocal_separation_material():
    return {'id': _uuid(), 'type': 'vocal_separation'}


def make_canvas_material():
    return {'id': _uuid(), 'type': 'canvas_color'}


def make_material_color_material():
    return {'id': _uuid()}


def make_material_animation_material():
    return {'id': _uuid(), 'type': 'sticker_animation'}


def make_subtitle_text_material(text, font_size, audio_seg_id, font_path, group_id=None):
    """字幕文本素材（sub_type=4, add_type=2, tts_auto_update=true）。"""
    content = {
        'styles': [{
            'effectStyle': None,
            'fill': {'content': {'render_type': 'solid', 'solid': {'color': [1, 1, 1]}}},
            'font': None,
            'range': [0, len(text)],
            'size': int(font_size),
        }],
        'text': text,
    }
    return {
        'id': _uuid(),
        'type': 'subtitle',
        'content': json.dumps(content, ensure_ascii=False),
        'words': {},
        'current_words': {},
        'combo_info': {},
        'caption_template_info': {'resource_id': '', 'path': ''},
        'layer_weight': 1,
        'line_spacing': 0.02,
        'shadow_color': '#000000cc',
        'shadow_smoothing': 1.0,
        'shadow_distance': 5.0,
        'shadow_point': {'x': 1.0182337649086284, 'y': 1.0182337649086284},
        'shadow_angle': -45.0,
        'border_width': 0.0,
        'text_color': '#FFFFFF',
        'font_size': int(font_size),
        'font_path': font_path,
        'initial_scale': 1.0,
        'text_to_audio_ids': [audio_seg_id],
        'sub_type': 4,
        'add_type': 2,
        'tts_auto_update': True,
        'group_id': group_id or ('aiwrite_%s' % _uuid()),
        'lyrics_template': {'resource_id': '', 'path': ''},
        'translate_original_text': text,
    }


def make_text_segment(text_material_id, start_us, dur_us, track_render_index,
                      render_index=14000, y_off=0.0):
    """字幕段。y_off：位置相对偏移（画布归一化，正=上移，如 0.08 ≈ 上移 8% 半高）。
    2026-09-04 用户要求"字幕位置全局统一微调"（避手机 UI/头像遮挡）：
    默认 0 = 模板原位置，改 --sub-y 整体平移，一次到位不用在剪映逐条拖。"""
    # start=0 时省略 start 字段，与剪映自身序列化格式一致（缺省即 0）
    tt = {'duration': dur_us}
    if start_us:
        tt['start'] = start_us
    return {
        'id': _uuid(),
        'target_timerange': tt,
        'render_timerange': {},
        'clip': {'scale': {'x': 1.0, 'y': 1.0},
                 'transform': {'x': 0.0, 'y': float(y_off or 0.0)}, 'flip': {}},
        'uniform_scale': {},
        'material_id': text_material_id,
        'extra_material_refs': [],
        'render_index': render_index,
        'enable_lut': False,
        'enable_adjust': False,
        'enable_hsl': False,
        'track_render_index': track_render_index,
        'track_attribute': 2,
        'responsive_layout': {},
        'enable_adjust_mask': False,
        'source': 'segmentsourcenormal',
    }


def make_tts_audio_material(text, src_dur_us, text_material_id, tone, placeholder_id, wav_name):
    return {
        'id': _uuid(),
        'type': 'text_to_audio',
        'name': text,
        'duration': src_dur_us,
        'path': '##_draftpath_placeholder_%s_##/textReading/%s' % (placeholder_id, wav_name),
        'text_id': text_material_id,
        'tone_type': tone.get('tone_type', ''),
        'resource_id': tone.get('resource_id', ''),
        'tone_speaker': tone.get('tone_speaker', ''),
        'tone_effect_name': tone.get('tone_effect_name', ''),
        'tone_platform': tone.get('tone_platform', 'sami'),
        'tone_emotion_name_key': tone.get('tone_emotion_name_key', 'voice_style_default'),
        'similiar_music_info': {},
        'tts_generate_scene': 'tts_update',
        'tts_benefit_info': {'benefit_type': 'none'},
    }


def make_tts_audio_segment(audio_seg_id, audio_material_id, start_us, src_dur_us, tgt_dur_us, speed, track_render_index):
    tt = {'duration': tgt_dur_us}
    if start_us:
        tt['start'] = start_us
    st = {'duration': src_dur_us}
    return {
        'id': audio_seg_id,
        'source_timerange': st,
        'target_timerange': tt,
        'render_timerange': {},
        'speed': round(float(speed), 2),
        'volume': 1.0,
        'material_id': audio_material_id,
        'extra_material_refs': [],
        'enable_lut': False,
        'enable_adjust': False,
        'enable_hsl': False,
        'track_render_index': track_render_index,
        'responsive_layout': {},
        'enable_adjust_mask': False,
        'source': 'segmentsourcenormal',
    }


def make_video_material(path, seg_dur_us, w, h, kind='video'):
    """素材条目。图片用剪映的 type='photo'（2026-09-15 起图片参与选片）。"""
    return {
        'id': _uuid(),
        'type': 'photo' if kind == 'image' else 'video',
        'duration': seg_dur_us,
        'path': path.replace('\\', '/'),
        'has_audio': False,
        'width': w,
        'height': h,
        'category_name': '',
        'material_id': '',
        'material_name': os.path.basename(path),
        'crop': {},
        'stable': {'time_range': {}},
        'matting': {'path': ''},
        'check_flag': 0,
        'video_algorithm': {'path': '', 'story_video_modify_video_config': {}},
        'beauty_face_auto_preset': {},
        'video_mask_stroke': {'resource_id': '', 'path': '', 'type': ''},
        'video_mask_shadow': {'resource_id': '', 'path': ''},
    }


def make_video_segment(video_material_id, start_us, seg_dur_us, scale,
                       track_render_index=0, src_dur=None, speed=1.0, src_start=0):
    """视频段。speed = source/target：>1 加快（素材长），<1 放慢（素材短）。

    素材容量不匹配时靠调速消化，避免定格画面（见 fit_clip_speed）。
    """
    src_dur = src_dur or seg_dur_us
    tt = {'duration': seg_dur_us}
    if start_us:
        tt['start'] = start_us
    return {
        'id': _uuid(),
        'source_timerange': ({'start': int(src_start), 'duration': src_dur}
                             if src_start else {'duration': src_dur}),
        'target_timerange': tt,
        'render_timerange': {},
        'speed': round(float(speed), 4),
        'volume': 0.0,
        'clip': {'scale': {'x': round(scale, 4), 'y': round(scale, 4)},
                 'transform': {'x': 0.0, 'y': 0.0}, 'flip': {}},
        'uniform_scale': {},
        'material_id': video_material_id,
        'extra_material_refs': [],
        'enable_hsl': False,
        'hdr_settings': {'mode': 1},
        'track_attribute': 1,
        'responsive_layout': {},
        'enable_adjust_mask': False,
        'source': 'segmentsourcenormal',
    }


def make_bgm_audio_material(bgm_cfg, bgm_record=None):
    """BGM 音乐素材：bgm_record 为 bgm_pool 解析出的**本机真实存在**的音乐记录。

    P05 §7 起：只从 bgm_record 取 path（不再回退模板缓存路径）。
    传 None / 无 path 时返回的素材 path 为空 —— 调用方应据此**不建 BGM 轨**。
    """
    rec = bgm_record or {}
    return {
        'id': _uuid(),
        'type': 'music',
        'name': rec.get('name') or bgm_cfg.get('name', ''),
        'duration': rec.get('duration') or 60000000,
        # P05 §6/§7：不再回退到模板里的 `_template_path`（那是管理员剪映的
        # 本地缓存绝对路径，换台电脑必然不存在）。没有真实记录就留空，
        # 由调用方决定是否建 BGM 轨。
        'path': rec.get('path') or '',
        'category_name': rec.get('category_name') or bgm_cfg.get('category', '推荐音乐'),
        'music_id': rec.get('music_id') or bgm_cfg.get('resource_id', ''),
        'app_id': rec.get('app_id') or 1775,
        'resource_id': rec.get('resource_id') or bgm_cfg.get('resource_id', ''),
        'category_id': rec.get('category_id') or '0',
        'request_id': rec.get('request_id') or '',
        'music_source': rec.get('music_source') or 'pgc',
        'similiar_music_info': {},
        'tts_benefit_info': {'benefit_type': 'none'},
    }


def make_bgm_audio_segments(bgm_material_id, music_dur_us, total_us, track_render_index):
    """BGM 段列表：音乐比总时长短时自动循环铺满（多段首尾相接）。"""
    segs, cursor = [], 0
    music_dur_us = max(int(music_dur_us) or total_us, 1)
    while cursor < total_us:
        seg_dur = min(music_dur_us, total_us - cursor)
        seg = {
            'id': _uuid(),
            'source_timerange': {'start': 0, 'duration': seg_dur},
            'target_timerange': {'duration': seg_dur},
            'render_timerange': {},
            'volume': 0.3,
            'material_id': bgm_material_id,
            'extra_material_refs': [],
            'enable_lut': False,
            'enable_adjust': False,
            'enable_hsl': False,
            'track_render_index': track_render_index,
            'responsive_layout': {},
            'enable_adjust_mask': False,
            'source': 'segmentsourcenormal',
        }
        if cursor > 0:
            seg['target_timerange']['start'] = cursor
        segs.append(seg)
        cursor += seg_dur
    return segs


def make_disclaimer_material(text, font_size, font_path):
    content = {
        'text': text,
        'styles': [{
            'fill': {'content': {'render_type': 'solid', 'solid': {'color': [1, 1, 1]}}},
            'font': None,
            'size': int(font_size),
            'range': [0, len(text)],
        }],
    }
    return {
        'id': _uuid(),
        'type': 'text',
        'content': json.dumps(content, ensure_ascii=False),
        'words': {},
        'current_words': {},
        'combo_info': {},
        'caption_template_info': {'resource_id': '', 'path': ''},
        'layer_weight': 1,
        'line_spacing': 0.02,
        'shadow_alpha': 0.9,
        'shadow_smoothing': 0.45,
        'shadow_distance': 5.0,
        'shadow_point': {'x': 0.6363961030678928, 'y': -0.6363961030678928},
        'shadow_angle': -45.0,
        'border_width': 0.08,
        'text_color': '#FFFFFF',
        'font_size': int(font_size),
        'font_path': font_path,
        'initial_scale': 1.0,
        'lyrics_template': {'resource_id': '', 'path': ''},
    }


def make_disclaimer_segment(mat_id, total_us, track_render_index):
    return {
        'id': _uuid(),
        'target_timerange': {'duration': total_us},
        'render_timerange': {},
        'clip': {'scale': {'x': 1.0761648745519714, 'y': 1.0761648745519714},
                 'transform': {'x': 0.0, 'y': -0.95578231292517}, 'flip': {}},
        'uniform_scale': {},
        'material_id': mat_id,
        'extra_material_refs': [],
        'render_index': 14047,
        'enable_lut': False,
        'enable_adjust': False,
        'enable_hsl': False,
        'track_render_index': track_render_index,
        'responsive_layout': {},
        'enable_adjust_mask': False,
        'source': 'segmentsourcenormal',
    }


def time_ms():
    import time
    return int(time.time() * 1000)


# ---------- 主构建 ----------

# 素材容量适配的速度边界
MAX_FAST = 2.0    # 最快 2 倍速（再快画面会跳）
MIN_SLOW = 0.5    # 最慢 0.5 倍速（再慢会明显拖沓）


def fit_clip_speed(avail_us, need_us, max_fast=MAX_FAST, min_slow=MIN_SLOW):
    """素材容量不匹配时用播放速度消化，返回 (speed, src_dur, note, ok)。

    规则：
      - 素材容量不足（短）→ 放慢播放来匹配，避免定格
      - 素材容量超出（长）→ 保持 1 倍速截取，不为了“播完整条”强行加速。

    speed = source / target。ok=False 表示连最慢速都扛不住，
    建议上层换个素材（或把这段拆开），不要硬撑。
    """
    if avail_us <= 0:
        return 1.0, need_us, '素材时长未知，按 1 倍速', True
    if abs(avail_us - need_us) <= 100000:      # 差 0.1s 内算正好
        return 1.0, avail_us, '', True
    if avail_us < need_us:
        # 素材短 → 放慢播放
        speed = avail_us / need_us
        if speed < min_slow:
            return (min_slow, int(need_us * min_slow),
                    '素材仅 %.1fs 却要撑 %.1fs，放慢到 %.1fx 仍不足，建议换素材'
                    % (avail_us / 1e6, need_us / 1e6, min_slow), False)
        return speed, avail_us, '素材偏短，放慢到 %.2fx 撑满 %.1fs' % (
            speed, need_us / 1e6), True
    # 素材长：保持原速截取，避免航拍/推镜因 1.5~2x 加速显得跳动。
    return (1.0, int(need_us),
            '素材偏长（%.1fs），保持 1 倍速截取 %.1fs'
            % (avail_us / 1e6, need_us / 1e6), True)


def snap_to_frame(us, fps=30):
    """把微秒时长对齐到帧网格。

    剪映里字幕/配音段的 target_timerange.duration 永远是整数帧（30fps → 33333.33us
    的倍数）。不对齐的话每段攒下几毫秒，十几段之后总时长会偏几十毫秒、末尾音画渐进错位。
    """
    frame = 1e6 / fps
    return int(round(round(us / frame) * frame))


def build_draft(script, style_key='3', materials_dir='', tone_cfg=None,
                draft_name='园区招商视频', font_path=None, city_hint='',
                bgm_choice='random', bgm_pool_list=None, tts_fn=None, fps=30,
                no_tts=True, park=None, video_orientation=None,
                sub_y_off=0.0, sub_font_size=None, material_plan=None,
                record_material_usage=True, install_dir='',
                allow_empty_materials=False, warnings=None):
    """生成剪映明文草稿 JSON dict。

    sub_y_off：字幕位置全局统一偏移（画布归一化，正=上移，0=模板原位置）。
    用户 2026-09-04 需求：整体上移/下移避手机 UI/头像遮挡，不必在剪映逐条拖。
    sub_font_size：覆盖字幕字号（默认用风格模板值，如 8.0）。

    font_path：不传则**运行时探测**本机可用中文字体（P05 §7）。
    探测不到时用空串，并往 ``warnings`` 里记一条 —— 不写死盘符与版本号。

    bgm_choice：**传 bgm_pool 解析出的记录 dict**（P05 起不再接受 'random'/'fixed'
    字符串）：只接受 ``os.path.isfile()`` 为真的路径；传 None / 路径不存在则
    **不生成 BGM 轨**，并记一条 warning（不再回退到模板里的管理员缓存路径）。

    tts_fn：可选回调 `tts_fn(subtitles, tone) -> [{'path','duration_us'}] | None`。
    传入则用它预先合成好的真实配音；不传则沿用旧路：占位素材 + 估算时长。

    no_tts（默认 True，2026-09-03 用户拍板）：**不生成配音轨**（纯字幕）。
    """
    styles = _load_json(os.path.join(CONFIG_DIR, 'default_styles.json'))
    common = styles['common']
    skey, style = resolve_style(style_key, styles['styles'])
    canvas = style.get('canvas', styles.get('canvas_default'))
    sub_cfg = style.get('subtitle', {})
    tts_cfg = style.get('tts', {})
    bgm_cfg = style.get('bgm', {})
    texts_cfg = style.get('texts', {})
    warn = warnings if warnings is not None else []

    # ---- 字体：运行时探测（P05 §7）----
    if not font_path:
        if DEFAULT_FONT_PATH and os.path.isfile(DEFAULT_FONT_PATH):
            font_path = DEFAULT_FONT_PATH                    # 仅当外部显式覆盖了常量
        else:
            font_path = resolve_font_path(install_dir=install_dir)
    if font_path and not os.path.isfile(font_path):
        warn.append('字体路径不存在，已置空交由剪映回退：%s' % font_path)
        font_path = ''
    if not font_path:
        warn.append('本机未找到可用中文字体（剪映资源目录与 Windows 字体都没有），'
                    '字幕字体交由剪映回退默认')
        print('[字体] ⚠ 本机未找到可用中文字体，草稿字体路径留空（剪映会回退默认字体）')
    else:
        print('[字体] %s' % font_path)

    # 1) 字幕切分
    subtitles = subtitle_split.split_subtitles(script, style, city_hint)
    # 2) 音色选择
    tone = pick_tone(skey, tone_cfg, style_name=style.get('name', '')) or tts_cfg
    tone['hook_speed'] = tts_cfg.get('hook_speed')
    tone['normal_speed'] = tts_cfg.get('normal_speed', common['speed_default'])

    # 3) 配音合成（可选）：拿到真实音频后，时间轴按真实时长排布
    tts_audio = None
    if tts_fn is not None:
        tts_audio = tts_fn(subtitles, tone)
        if tts_audio and len(tts_audio) != len(subtitles):
            print('[警告] 配音数量 %d ≠ 字幕数 %d，回退到估算时长'
                  % (len(tts_audio), len(subtitles)))
            tts_audio = None
    if tts_audio:
        print('[配音] 已合成 %d 段真实配音，总时长 %.2f s（无需再到剪映点朗读）'
              % (len(tts_audio), sum(a['duration_us'] for a in tts_audio) / 1e6))

    # 3.5) 素材匹配：必须排在配音之后 —— 匹配要知道每条字幕的**真实**时长，
    #      用估算值会差出 20 秒（实测 30.2s 估算 vs 49.6s 真实），素材容量
    #      判断就全错了
    sub_durs = []
    for i, sub in enumerate(subtitles):
        if tts_audio:
            sub_durs.append(snap_to_frame(tts_audio[i]['duration_us'], fps))
        else:
            sub_durs.append(int(sub['tgt_dur_ms'] * 1000))
    if material_plan:
        import storyboard as _storyboard
        matched = _storyboard.matched_from_plan(material_plan, subtitles, script=script)
    else:
        # P05 §3：库层也做一次空素材库拦截（与 CLI / GUI / 分镜预览同一判据）。
        # 调用方漏检时在这里立刻停下，而不是产出一条"没有画面但一切正常"的草稿。
        _mv = material_matcher.validate_materials_dir(materials_dir)
        if not _mv['ok']:
            if not allow_empty_materials:
                raise ValueError(_mv['msg'])
            warn.append(_mv['msg'].splitlines()[0])
            print('[素材库] ⚠ %s' % _mv['msg'].replace('\n', '\n          '))
        matched = material_matcher.match_materials(
            subtitles, materials_dir, sub_durations=sub_durs, park=park,
            video_orientation=video_orientation, script_text=script,
            record_usage=record_material_usage)

    # 4) 加载模板基底：**只取顶层元数据**（P05 起模板已瘦身为元数据）。
    #    模板里不再保存任何本机缓存路径，BGM 一律走 bgm_pool 动态解析。
    template = _load_json(STYLE_TEMPLATE[skey])

    # 4.5) BGM：只接受"本机真实存在"的记录，否则不生成 BGM 轨
    bgm_record = bgm_choice if isinstance(bgm_choice, dict) else None
    bgm_available = bool(bgm_record and bgm_record.get('path')
                         and os.path.isfile(bgm_record['path']))
    if bgm_record and not bgm_available:
        warn.append('BGM 文件不存在，已按无 BGM 处理：%s' % bgm_record.get('path'))
        print('[BGM] ⚠ 指定曲目在本机不存在，本次不生成 BGM 轨：%s'
              % bgm_record.get('path'))
        bgm_record = None
    if bgm_record is None:
        msg = '本机未找到可用BGM，已生成无BGM草稿'
        if msg not in warn:
            warn.append(msg)
        print('[BGM] ⚠ %s（不写入任何不存在的路径）' % msg)

    canvas_w, canvas_h = canvas['width'], canvas['height']
    placeholder_id = str(uuid.uuid4()).upper()

    # 辅助素材容器（每个段需要独立引用）
    aux_mats = {
        'canvases': [], 'beats': [], 'material_animations': [],
        'placeholder_infos': [], 'speeds': [],
        'sound_channel_mappings': [], 'material_colors': [], 'vocal_separations': [],
    }

    def attach_text_aux(seg):
        anim = make_material_animation_material()
        aux_mats['material_animations'].append(anim)
        seg['extra_material_refs'] = [anim['id']]
        return seg

    def attach_audio_aux(seg, with_beats=True):
        refs = []
        sp = make_speed_material()
        aux_mats['speeds'].append(sp)
        refs.append(sp['id'])
        ph = make_placeholder_info_material()
        aux_mats['placeholder_infos'].append(ph)
        refs.append(ph['id'])
        if with_beats:
            bt = make_beats_material()
            aux_mats['beats'].append(bt)
            refs.append(bt['id'])
        scm = make_sound_channel_mapping_material()
        aux_mats['sound_channel_mappings'].append(scm)
        refs.append(scm['id'])
        vs = make_vocal_separation_material()
        aux_mats['vocal_separations'].append(vs)
        refs.append(vs['id'])
        seg['extra_material_refs'] = refs
        return seg

    def attach_video_aux(seg):
        refs = []
        sp = make_speed_material()
        aux_mats['speeds'].append(sp)
        refs.append(sp['id'])
        ph = make_placeholder_info_material()
        aux_mats['placeholder_infos'].append(ph)
        refs.append(ph['id'])
        cv = make_canvas_material()
        aux_mats['canvases'].append(cv)
        refs.append(cv['id'])
        anim = make_material_animation_material()
        aux_mats['material_animations'].append(anim)
        refs.append(anim['id'])
        scm = make_sound_channel_mapping_material()
        aux_mats['sound_channel_mappings'].append(scm)
        refs.append(scm['id'])
        mc = make_material_color_material()
        aux_mats['material_colors'].append(mc)
        refs.append(mc['id'])
        vs = make_vocal_separation_material()
        aux_mats['vocal_separations'].append(vs)
        refs.append(vs['id'])
        seg['extra_material_refs'] = refs
        return seg

    text_materials, text_segments = [], []
    tts_materials, tts_segments = [], []
    track_render = 1  # 字幕文本轨道渲染序号
    cursor_us = 0  # 时间轴游标：每条字幕/TTS 段的起点（累积）
    sub_starts = []  # 每条字幕的起点（视频段排布用）
    tgt_list = []    # 每条字幕的实际展示时长（真实配音时与估算值不同）
    for i, sub in enumerate(subtitles):
        real = tts_audio[i] if tts_audio else None
        if real:
            # 语速已在合成时生效，剪映侧不再变速；时长对齐到帧网格
            src_us = int(real['duration_us'])
            tgt_us = snap_to_frame(src_us, fps)
            speed = 1.0
            wav_name = os.path.basename(real['path'])
        else:
            # 估算时长：必须对齐 30fps 帧网格（33333.33us 的整数倍）。
            # 不做对齐的话每段攒下几毫秒误差，十几段后字幕与画面渐进错位
            # （2026-09-03 校准字幕时长后暴露：5040000us = 151.2 帧，非整数）
            tgt_us = snap_to_frame(int(sub['tgt_dur_ms'] * 1000), fps)
            src_us = int(sub['src_dur_ms'] * 1000)
            speed = sub['speed']
            wav_name = 'tts_%02d_%s.wav' % (i, _uuid().lower())
        sub_starts.append(cursor_us)
        tgt_list.append(tgt_us)
        # 顺序：先建 TTS 素材（text_id 稍后回填），再建字幕素材指向它
        audio_seg_id = _uuid()
        font_size = sub_font_size if sub_font_size else sub_cfg.get('font_size', 8.0)
        if no_tts:
            # 纯字幕模式（默认）：不创建配音素材、不建配音段。
            # 字幕必须解除音频关联并关掉自动重合成，否则剪映会因为
            # text_to_audio_ids 指向不存在的素材而把字幕标成"待合成"。
            am = None
            tm = make_subtitle_text_material(sub['text'], font_size,
                                             '', font_path,
                                             group_id='aiwrite_%d_%s' % (i, _uuid()))
            tm['text_to_audio_ids'] = []
            tm['tts_auto_update'] = False
        else:
            am = make_tts_audio_material(sub['text'], src_us, '', tone,
                                         placeholder_id, wav_name)
            if real:
                # edge-tts 模式：素材改成 type='audio'，剪映就不会显示"文本·朗读"
                # 按钮，也不会主动调云端 API 重新合成 —— 否则按素材里残留的
                # 剪映音色名（如 moqingxuan_meet_24k）调云端会失败，剪映会把
                # 整段标成"待合成"导致静音。path 改成绝对路径更稳。
                am['type'] = 'audio'
                am['name'] = '配音-' + sub['text'][:18]
                am['path'] = real['path']  # 已经是绝对路径（edge-tts 输出的）
                for k in ('tone_type', 'tone_effect_name', 'tone_emotion_name_key',
                          'similiar_music_info', 'tts_benefit_info',
                          'tts_generate_scene'):
                    am.pop(k, None)
            tm = make_subtitle_text_material(sub['text'], font_size,
                                             am['id'], font_path,
                                             group_id='aiwrite_%d_%s' % (i, _uuid()))
            if real:
                # 字幕不再关联配音素材（因为配音不是 text_to_audio 类型了），
                # 关闭自动重合成。字幕和配音靠 target_timerange 对位。
                tm['text_to_audio_ids'] = []
                tm['tts_auto_update'] = False
            am['text_id'] = tm['id']
        text_materials.append(tm)
        text_segments.append(attach_text_aux(make_text_segment(tm['id'], cursor_us, tgt_us, track_render,
                                                               render_index=14000 + i, y_off=sub_y_off)))
        if not no_tts:
            tts_materials.append(am)
            if real:
                # edge 模式：type='audio'，不要 placeholder_info（那是 TTS 专用，
                # 留着会让剪映误以为是占位要重合成），其他常规音频辅助都加上
                seg = make_tts_audio_segment(audio_seg_id, am['id'], cursor_us, src_us, tgt_us,
                                             speed, track_render + 1)
                refs = []
                sp = make_speed_material(); aux_mats['speeds'].append(sp); refs.append(sp['id'])
                scm = make_sound_channel_mapping_material(); aux_mats['sound_channel_mappings'].append(scm); refs.append(scm['id'])
                vs = make_vocal_separation_material(); aux_mats['vocal_separations'].append(vs); refs.append(vs['id'])
                seg['extra_material_refs'] = refs
                tts_segments.append(seg)
            else:
                tts_segments.append(attach_audio_aux(
                    make_tts_audio_segment(audio_seg_id, am['id'], cursor_us, src_us, tgt_us,
                                           speed, track_render + 1)))
        cursor_us += tgt_us

    total_us = cursor_us
    if total_us <= 0:
        # 兜底：纯字幕模式下 tts_segments 为空，要退回用字幕段算总时长
        fallback_segs = tts_segments or text_segments
        total_us = sum(seg['target_timerange']['duration'] for seg in fallback_segs)

    # 5) 视频轨道：把连续共用同一素材的字幕合并为一段；
    #    同一素材被隔开复用时按 run 建独立段（素材按 path 复用 material）
    video_materials2, video_segments2, material_by_path = [], [], {}
    n = len(subtitles)
    i = 0
    while i < n:
        m = matched[i] if i < len(matched) else None
        if not m or not m.get('path'):
            i += 1
            continue
        path = m['path']
        run_end = i
        while run_end < n and matched[run_end] is not None \
                and matched[run_end].get('path') == path:
            run_end += 1
        # 视频段时长 = 所覆盖字幕的时长和，**不设最小值下限**。
        # 旧代码 max(sum, 1.5s) 在新切换规则下会产 bug：单条 1.2s 短字幕独立成段时
        # 被拉到 1.5s，而下一段仍按字幕真实起点 sub_starts 放 → 相邻视频段重叠
        # 300ms；剪映打开后自动顺次排开，切点漂移、视频轨总长超出时间线
        # （实测 29.5s vs 28.9s），时间线上字幕/视频边界看起来"不整齐"。
        # 字幕本身已 ≥1.2s（MIN_DUR_MS），无需视频侧再兜底。
        run_dur = sum(tgt_list[k] for k in range(i, run_end))
        if path not in material_by_path:
            wh = probe_media_display_size(path)
            w, h = (wh if wh and wh[0] > 0 and wh[1] > 0 else (1920, 1080))
            scale = max(canvas_w / w, canvas_h / h, 1.0)
            file_dur = probe_media_duration(path)
            # 素材表记真实时长：以前用 max(file_dur, run_dur) 会把"素材不够"
            # 这件事掩盖掉，导致静默定格
            vm = make_video_material(path, file_dur or run_dur, w, h,
                                     kind=(m.get('kind') or 'video'))
            material_by_path[path] = (vm, scale, file_dur)
            video_materials2.append(vm)
        else:
            vm, scale, file_dur = material_by_path[path]
        # 容量适配：用播放速度消化素材时长与展示时长的差，避免定格画面
        speed, src_dur, note, ok = fit_clip_speed(file_dur or 0, run_dur)
        if (m.get('kind') or 'video') == 'image':
            note = '图片素材，按 1 倍速铺满 %.1fs' % (run_dur / 1e6)
        if note:
            print('      [%s] %s' % (os.path.basename(path)[:26], note))
        # 长素材从中段截取，避开常见的起飞、对焦和手持准备段。
        src_start = (max(0, int(((file_dur or 0) - src_dur) / 2))
                     if speed == 1.0 and (file_dur or 0) > src_dur else 0)
        video_segments2.append(attach_video_aux(
            make_video_segment(vm['id'], sub_starts[i], run_dur, scale, 0,
                               src_dur=src_dur, speed=speed, src_start=src_start)))
        i = run_end

    # 6) BGM + 免责花字
    #    P05 §7：只有确认曲目在本机真实存在（bgm_available）时才生成 BGM 轨。
    #    找不到就**整条轨都不建**——老实现会写一条指向空路径的 music 素材，
    #    在剪映里表现为"一条无声的 BGM 轨"，看着像成功，实际是坏的。
    if bgm_available:
        bgm_mat = make_bgm_audio_material(bgm_cfg, bgm_record)
        bgm_segs = [attach_audio_aux(s, with_beats=False) for s in
                    make_bgm_audio_segments(bgm_mat['id'], bgm_mat.get('duration'),
                                            total_us, track_render + 2)]
    else:
        bgm_mat = None
        bgm_segs = []

    disclaimer_cfg = texts_cfg.get('disclaimer', {})
    d_text = disclaimer_cfg.get('text', common.get('disclaimer', '注：以上均为建筑面积'))
    d_font = disclaimer_cfg.get('font_size', 5.0)
    d_mat = make_disclaimer_material(d_text, d_font, font_path)
    d_seg = attach_text_aux(make_disclaimer_segment(d_mat['id'], total_us, track_render + 3))

    # 7) 组装 tracks
    #    纯字幕模式（no_tts）不建配音轨 —— 留一条空 audio 轨没意义，
    #    剪映里还会显示成一条空的音轨占位。
    tracks = [
        {'id': _uuid(), 'type': 'video', 'attribute': 1, 'is_default_name': True,
         'segments': video_segments2},
        {'id': _uuid(), 'type': 'text', 'attribute': 2, 'flag': 1, 'is_default_name': True,
         'segments': text_segments},
    ]
    if not no_tts:
        tracks.append({'id': _uuid(), 'type': 'audio', 'is_default_name': True,
                       'segments': tts_segments})
    if bgm_segs:                      # 无可用 BGM 时整条轨不建（P05 §7）
        tracks.append({'id': _uuid(), 'type': 'audio', 'is_default_name': True,
                       'segments': bgm_segs})
    tracks.append({'id': _uuid(), 'type': 'text', 'is_default_name': True,
                   'segments': [d_seg]})

    # 8) materials：保留模板的辅助空表结构
    mats = {
        'videos': video_materials2,
        'audios': tts_materials + ([bgm_mat] if bgm_mat else []),
        'texts': text_materials + [d_mat],
        'effects': [],
        'stickers': [],
        'canvases': aux_mats['canvases'],
        'beats': aux_mats['beats'],
        'material_animations': aux_mats['material_animations'],
        'placeholder_infos': aux_mats['placeholder_infos'],
        'speeds': aux_mats['speeds'],
        'text_templates': [],
        'sound_channel_mappings': aux_mats['sound_channel_mappings'],
        'material_colors': aux_mats['material_colors'],
        'loudnesses': [],
        'vocal_separations': aux_mats['vocal_separations'],
    }

    draft = {
        'id': str(uuid.uuid4()).upper(),
        'version': template.get('version', 360000),
        'new_version': template.get('new_version', common['draft_new_version']),
        'duration': total_us,
        'color_space': template.get('color_space', 0),
        'config': template.get('config', {'video_mute': True}),
        'canvas_config': {'ratio': canvas.get('ratio', '9:16'), 'width': canvas_w, 'height': canvas_h},
        'tracks': tracks,
        'materials': mats,
        'keyframes': {},
        # P05 §6：平台信息里的 device_id / hard_disk_id / mac_address 是
        # 管理员机器的硬件标识，必须洗掉再写进草稿，否则会扩散到全团队。
        'platform': sanitize_platform(template.get('platform')),
        'last_modified_platform': sanitize_platform(template.get('last_modified_platform')),
        'render_index_track_mode_on': template.get('render_index_track_mode_on', True),
        'static_cover_image_path': '',
        'path': '',
        'uneven_animation_template_info': {},
        'smart_ads_info': {},
        'function_assistant_info': {'fps': {}},
    }

    info = {
        'style_key': skey,
        'style_name': style.get('name', ''),
        'subtitles': subtitles,
        'total_dur_us': total_us,
        'total_dur_s': round(total_us / 1e6, 2),
        'video_materials': [{'path': m['path'], 'width': m['width'], 'height': m['height'],
                             'name': m['material_name']} for m in video_materials2],
        'video_segments': len(video_segments2),
        'tone': tone,
        'placeholder_id': placeholder_id,
        # P05 §7：无可用 BGM 时 name/path 一律留空（**不伪装**），
        # available 明确告诉回执/横幅"这条草稿没有 BGM"。
        'bgm': {'name': (bgm_mat or {}).get('name', ''),
                'path': (bgm_mat or {}).get('path', ''),
                'duration_s': round(((bgm_mat or {}).get('duration') or 0) / 1e6, 1),
                'segments': len(bgm_segs),
                'available': bool(bgm_available)},
        'storyboard_plan_used': bool(material_plan),
    }
    return draft, info


# ---------- draft_meta_info.json 生成 ----------

def build_meta_info(draft, draft_name, draft_fold_path, root_path=None):
    """由草稿内容生成 draft_meta_info.json 字典。"""
    import time
    now = time.time()
    tm = int(now * 1e6)
    videos = []
    for v in draft['materials'].get('videos', []):
        videos.append({
            'ai_group_type': '', 'create_time': -1, 'duration': v.get('duration', 0),
            'enter_from': 0, 'extra_info': v.get('material_name', ''),
            'file_Path': v.get('path', ''), 'height': v.get('height', 0),
            'id': v.get('id', ''), 'import_time': -1, 'import_time_ms': -1,
            'item_source': 1, 'material_color_tag': '', 'md5': '',
            'metetype': 'video', 'roughcut_time_range': {'duration': v.get('duration', 0), 'start': 0},
            'sub_time_range': {'duration': -1, 'start': -1}, 'type': 0,
            'width': v.get('width', 0),
        })
    musics = []
    for a in draft['materials'].get('audios', []):
        if a.get('type') == 'music':
            rid = a.get('resource_id', '')
            musics.append({
                'ai_group_type': '', 'create_time': -1, 'duration': a.get('duration', 0),
                'enter_from': 0,
                'extra_info': json.dumps({
                    'musicPanelType': 'musictype_audio', 'resouceType': 'Platform_SongLib',
                    'resourceID': rid, 'signHeader': False, 'url': '',
                }, ensure_ascii=False),
                'file_Path': a.get('path', ''), 'height': 0, 'id': a.get('id', ''),
                'import_time': -1, 'import_time_ms': -1, 'item_source': 1,
                'material_color_tag': '', 'md5': '', 'metetype': 'music',
                'roughcut_time_range': {'duration': a.get('duration', 0), 'start': 0},
                'sub_time_range': {'duration': -1, 'start': -1}, 'type': 8,
                'width': 0,
            })
            musics.append({
                'ai_group_type': '', 'create_time': -1, 'duration': a.get('duration', 0),
                'enter_from': 0,
                'extra_info': json.dumps({
                    'musicPanelType': 'musictype_audio', 'resouceType': 'Platform_SongLib',
                    'resourceID': rid, 'signHeader': True, 'url': '',
                }, ensure_ascii=False),
                'file_Path': a.get('path', ''), 'height': 0, 'id': _uuid(),
                'import_time': -1, 'import_time_ms': -1, 'item_source': 1,
                'material_color_tag': '', 'md5': '', 'metetype': 'music',
                'roughcut_time_range': {'duration': a.get('duration', 0), 'start': 0},
                'sub_time_range': {'duration': -1, 'start': -1}, 'type': 8,
                'width': 0,
            })
    material_groups = []
    for t in (0, 1, 2, 3, 6, 7, 8):
        group = {'type': t, 'value': []}
        if t == 0:
            group['value'] = videos
        elif t == 8:
            group['value'] = musics
        material_groups.append(group)

    meta = {
        'cloud_draft_cover': False,
        'cloud_draft_sync': False,
        'cloud_package_completed_time': '',
        'draft_cloud_capcut_purchase_info': '',
        'draft_cloud_last_action_download': False,
        'draft_cloud_package_type': '',
        'draft_cloud_purchase_info': '',
        'draft_cloud_template_id': '',
        'draft_cloud_tutorial_info': '',
        'draft_cloud_videocut_purchase_info': '',
        'draft_cover': 'draft_cover.jpg',
        'draft_deeplink_url': '',
        'draft_enterprise_info': {'draft_enterprise_extra': '', 'draft_enterprise_id': '',
                                  'draft_enterprise_name': '', 'enterprise_material': []},
        'draft_fold_path': draft_fold_path,
        'draft_id': draft['id'],
        'draft_is_ae_produce': False,
        'draft_is_ai_packaging_used': False,
        'draft_is_ai_shorts': False,
        'draft_is_ai_translate': False,
        'draft_is_article_video_draft': False,
        'draft_is_cloud_temp_draft': False,
        'draft_is_from_deeplink': 'false',
        'draft_is_infinite_canvas_draft': False,
        'draft_is_invisible': False,
        'draft_is_pippit_draft': False,
        'draft_is_web_article_video': False,
        'draft_materials': material_groups,
        'draft_materials_copied_info': [],
        'draft_name': draft_name,
        'draft_need_rename_folder': False,
        'draft_new_version': draft.get('new_version', '164.0.0'),
        'draft_removable_storage_device': os.path.splitdrive(draft_fold_path)[0] or 'C:',
        'draft_root_path': root_path or os.path.dirname(draft_fold_path),
        'draft_segment_extra_info': [],
        'draft_timeline_materials_size_': 0,
        'draft_type': '',
        'draft_web_article_video_enter_from': '',
        'pippit_avatar_url': '',
        'pippit_extra_info': '',
        'pippit_id': '',
        'pippit_user_name': '',
        'tm_draft_cloud_completed': '',
        'tm_draft_cloud_entry_id': -1,
        'tm_draft_cloud_modified': 0,
        'tm_draft_cloud_parent_entry_id': -1,
        'tm_draft_cloud_space_id': -1,
        'tm_draft_cloud_user_id': -1,
        'tm_draft_create': tm,
        'tm_draft_modified': tm,
        'tm_draft_removed': 0,
        'tm_duration': draft.get('duration', 0),
    }
    return meta


# ---------- 独立自测 ----------

if __name__ == '__main__':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    demo_script = ('真没想到！南京还有租金5块的全新国资厂房！厂房就在南京智能制造产业园，'
                   '园区位于南京六合主城区，过桥十到十五分钟直通栖霞主城，交通通勤十分便捷。'
                   '现新建八栋生产厂房，超多户型任选，可整租可分租。'
                   '厂房层高4.9米至10.5米，一楼可配10吨航车。'
                   '水电蒸汽配套完善，还配有办公楼、食堂、宿舍等，满足企业生产生活所需。'
                   '这样的厂房租金仅需4毛。现在入驻园区还能享租金和产业方面的扶持政策。')
    import sys as _sys
    style_key = _sys.argv[1] if len(_sys.argv) > 1 else '3'
    materials = _sys.argv[2] if len(_sys.argv) > 2 else os.path.join(_SKILL_DIR, '..', '_test_materials')
    draft, info = build_draft(demo_script, style_key=style_key,
                              materials_dir=materials,
                              draft_name='自测-%s' % style_key)
    out = os.path.join(_HERE, '_test_draft_%s.json' % style_key)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(draft, f, ensure_ascii=False, indent=1)
    print('风格:', info['style_name'], '| 字幕', len(info['subtitles']), '条 | 总时长', info['total_dur_s'], 's')
    print('TTS 音色:', info['tone'].get('tone_type'), '/', info['tone'].get('tone_speaker'))
    print('视频素材:', len(info['video_materials']), '个 | 视频段:', info['video_segments'])
    for vm in info['video_materials']:
        print('  ', vm['width'], 'x', vm['height'], os.path.basename(vm['path']))
    print('输出:', out)
