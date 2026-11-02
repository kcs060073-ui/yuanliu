# -*- coding: utf-8 -*-
"""素材匹配模块：从素材目录按语义匹配横版视频素材。

素材命名约定（与模板一致）：
  园区门口/大门/入口、俯拍/航拍、地铁/地铁站、高速/高速口、
  高铁站/火车站、厂房/车间/生产线、宿舍、食堂、办公楼/写字楼、
  配套/环境/园区全景、租/户型/分割...

策略：
1. 遍历素材目录，按文件名关键词归类到语义桶
2. 按脚本字幕的语义点（由脚本解析给出 keywords）依次取素材
3. 每个素材覆盖 1~3 条字幕（对齐模板：9 素材 / 17 字幕）
"""
import os
import json
import re
import random

# 语义桶：关键词 → 类别
#
# 命名规范（用户定的）：<语义>_<横版|竖版>.mp4
# 推荐语义词：园区门口/大门、全景/航拍、交通、地铁、高速、高铁站、
#            厂房、厂房内部、宿舍、办公、食堂、配套/环境
# 例：全景_横版.mp4  厂房内部_横版.mp4  交通_横版.mp4
#
# 注意 BUCKET_ORDER 决定匹配优先级：**具体的排在通用的前面**。
# 比如"厂房内部"必须先命中 inside，否则会被 factory 的"厂房"抢走。
# ---------------------------------------------------------------------------
# 2026-09-09 起，语义规则外置到 config/semantics.json（P0-②，人人可改）。
# 下面这些**变量名保持不变**（SEMANTIC_BUCKETS / BUCKET_ORDER / BLOCK_BUCKETS /
# XX_KWS …），外部模块（workbench_gui、replace_seg、tests）照旧写 mm.XXX，
# 因此外置对调用方完全透明。
#
# 加载失败（文件缺失 / JSON 语法错 / 结构校验不过）会抛 SemanticsError 并终止，
# **不用内置默认值静默兜底** —— 悄悄用旧规则出片，比停下来更难查。
# ---------------------------------------------------------------------------
import sys as _sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)
from semantics_config import get_semantics as _get_semantics

try:
    import park_rules as _pr
except Exception:
    _pr = None

_S = _get_semantics()

SEMANTIC_BUCKETS = _S['semantic_buckets']
BUCKET_ORDER = _S['bucket_order']


def _probe_duration(path):
    """探测视频时长（微秒）。延迟导入避免与 draft_builder 的循环依赖。"""
    try:
        from draft_builder import probe_media_duration
        return probe_media_duration(path) or 0
    except Exception:
        return 0


def _is_root_layout(materials_dir):
    """判断 materials_dir 是否为「集中根目录」布局：含 _shared/ 或 _parks/。"""
    if not materials_dir or not os.path.isdir(materials_dir):
        return False
    return (os.path.isdir(os.path.join(materials_dir, '_shared')) or
            os.path.isdir(os.path.join(materials_dir, '_parks')))


def load_manifest(root):
    """读取根目录下的 _manifest.json（声明 park 别名与通用 bucket）。无则返回 None。"""
    p = os.path.join(root, '_manifest.json')
    if os.path.isfile(p):
        try:
            return json.load(open(p, encoding='utf-8'))
        except Exception:
            return None
    return None


def resolve_park(root, park):
    """把友好园区名解析成 _parks/ 下的文件夹名（通过 _manifest.json 的 aliases）。"""
    if not park:
        return park
    manifest = load_manifest(root)
    if manifest:
        parks = manifest.get('parks', {})
        if park in parks:
            return park
        for folder, info in parks.items():
            if not isinstance(info, dict):
                continue
            if park in info.get('aliases', []):
                return folder
    return park


def _orientation_of(dir_chain, filename):
    """标注朝向：目录链（横屏/竖屏层）优先，文件名（横版/竖版）兜底。

    这只是「人的标注」，不代表真实画面 —— 见 _resolve_orientation。
    """
    for d in reversed(dir_chain or []):
        if '竖屏' in d or '竖版' in d:
            return 'portrait'
        if '横屏' in d or '横版' in d:
            return 'landscape'
    for kw in ('竖版', '竖屏'):
        if kw in filename:
            return 'portrait'
    for kw in ('横版', '横屏'):
        if kw in filename:
            return 'landscape'
    return None


# 标注与真实画面冲突的记录（本次扫描内），用于打印摘要
_ORIENT_CONFLICTS = []


def _resolve_orientation(path, tag, kind='video'):
    """真实朝向优先，标注兜底（2026-11-02 用户拍板修复）。

    为什么以画面为准：标注只表达人的意图，像素决定素材能不能铺满画布。
    实测两类事故都出在标注上——
      ① 无标注桶横竖混装（南京/专业设施 8 条里有 1280x720 也有 720x1280），
         竖版会捞到横屏 → 成片黑边；
      ② 手工放错目录（北京 医械城2期/竖版 里有 3840x2160、嘉兴 高铁站/竖屏 里有横屏）。
    图片不逐张探测（成本高且无旋转信息）：仅当无标注时才探一次。
    """
    real = None
    if kind == 'video' or tag is None:
        try:
            from draft_builder import probe_media_orientation
            real = probe_media_orientation(path)
        except Exception:
            real = None
    if real:
        if tag and tag != real:
            _ORIENT_CONFLICTS.append((os.path.basename(path), tag, real))
        return real
    return tag


def _print_orientation_conflicts(limit=12):
    if not _ORIENT_CONFLICTS:
        return
    cn = {'portrait': '竖', 'landscape': '横'}
    print('[方向校正] %d 个素材标注与实际画面不符，已按实际画面处理：'
          % len(_ORIENT_CONFLICTS))
    for name, tag, real in _ORIENT_CONFLICTS[:limit]:
        print('    %s  标注=%s屏 → 实际=%s屏' % (name, cn.get(tag, tag), cn.get(real, real)))
    if len(_ORIENT_CONFLICTS) > limit:
        print('    ……另有 %d 个' % (len(_ORIENT_CONFLICTS) - limit))
    del _ORIENT_CONFLICTS[:]


def _scan_one_dir(d, with_duration=True):
    """扫描单个目录（递归），返回 items。

    item 结构 {path, name, bucket, duration, dir_chain, orientation}：
    - bucket 由「目录链语义 + 文件名兜底」判定（见 classify_with_dir）
    - dir_chain 记录文件相对扫描根的各层目录名（供语义/横竖屏判断）
    - orientation: 'portrait'/'landscape'/None（横屏/竖屏 目录层或 横版/竖版 文件名）
    """
    if not d or not os.path.isdir(d):
        return []
    exts = VIDEO_EXTS + IMAGE_EXTS
    items = []
    for root, _, fnames in os.walk(d):
        for fn in fnames:
            if fn.lower().endswith(exts):
                fp = os.path.join(root, fn)
                name = os.path.splitext(fn)[0]
                kind = media_kind(fp)
                if kind == 'image' and not image_ok(fp):
                    continue          # 太小/维度异常：不进池（避免放大糊、缩放崩）
                rel = os.path.relpath(root, d)
                dir_chain = [p for p in rel.split(os.sep) if p] if rel != '.' else []
                bucket = classify_with_dir(dir_chain, name)
                # 图片没有固有时长 → 0，构建期按 1 倍速铺满（fit_clip_speed 走 avail<=0 分支）
                dur = _probe_duration(fp) if (with_duration and kind == 'video') else 0
                tag = _orientation_of(dir_chain, name)
                items.append({'path': fp, 'name': name, 'bucket': bucket, 'kind': kind,
                              'duration': dur, 'dir_chain': dir_chain,
                              'orientation': _resolve_orientation(fp, tag, kind)})
    return items


def scan_materials(materials_dir, with_duration=True, park=None):
    """扫描素材目录，返回 list of {path, name, bucket, duration}。

    三种用法（自动识别）：
    1. 集中根目录 + park 指定：扫 `_shared/` + `_parks/<park>/`，
       该 park 缺失的 bucket 自动回退到 _shared 里的通用素材。
    2. 集中根目录但没指定 park：扫整个根（legacy / 单目录回退）。
    3. 普通文件夹（无 _shared/_parks）：原样递归扫描。
    """
    if not materials_dir or not os.path.isdir(materials_dir):
        return []
    if _is_root_layout(materials_dir):
        if park:
            folder = resolve_park(materials_dir, park)
            shared = _scan_one_dir(os.path.join(materials_dir, '_shared'), with_duration)
            park_dir = os.path.join(materials_dir, '_parks', folder)
            park_items = _scan_one_dir(park_dir, with_duration) if os.path.isdir(park_dir) else []
            return shared + park_items
        # 根布局但没给 park：扫整个根
        return _scan_one_dir(materials_dir, with_duration)
    return _scan_one_dir(materials_dir, with_duration)


# 素材类型（2026-09-15）：图片也参与选片。剪映里图片素材写 type='photo'。
VIDEO_EXTS = ('.mp4', '.mov', '.avi', '.mkv')
IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.jfif')


def media_kind(path):
    """'image' / 'video'（按扩展名；未知按 video 处理以保持旧行为）。"""
    return 'image' if os.path.splitext(path or '')[1].lower() in IMAGE_EXTS else 'video'


def count_media(materials_dir):
    """快速统计素材目录里的视频/图片数量（只按扩展名，不探时长）。

    专门给「素材库是否可用」的判断用：`scan_materials` 要探每个文件的时长，
    548 条素材要好几秒 —— 生成前的拦截检查不该付这个代价。
    """
    videos = images = 0
    if not materials_dir or not os.path.isdir(materials_dir):
        return 0, 0
    for root, dirs, fnames in os.walk(materials_dir):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for fn in fnames:
            ext = os.path.splitext(fn)[1].lower()
            if ext in VIDEO_EXTS:
                videos += 1
            elif ext in IMAGE_EXTS:
                images += 1
    return videos, images


def validate_materials_dir(materials_dir):
    """素材库有效性判断 —— **GUI 生成 / 批量生成 / 分镜预览 / CLI 出片共用同一套**。

    为什么单独抽一个函数（P05 §3）
    ------------------------------
    以前素材库为空时，流程会"成功"生成一条没有画面的草稿：字幕、BGM、
    时间轴都在，视频轨 0 段。界面给的是绿勾和「生成完成」，打开剪映才发现
    整条片子是黑底字幕 —— 对同事来说这比直接报错糟糕得多，因为**它不会被发现**。

    返回 dict：

    ==========  ==================================================
    code        含义
    ==========  ==================================================
    OK          至少 1 个系统支持的视频文件
    NO_PATH     没选素材库（空串）
    NOT_DIR     路径不存在 / 不是目录
    EMPTY       目录存在，但一个系统支持的视频都没有
    ==========  ==================================================
    """
    raw = (materials_dir or '').strip()
    if not raw:
        return {'ok': False, 'code': 'NO_PATH', 'path': '', 'videos': 0, 'images': 0,
                'msg': '还没有选择素材库目录。\n'
                       '  请在工作台「素材库与输出」里选择本机已下载完整的素材库，'
                       '或从团队共享盘整份拷贝一份。'}
    if not os.path.isdir(raw):
        return {'ok': False, 'code': 'NOT_DIR', 'path': raw, 'videos': 0, 'images': 0,
                'msg': '素材库路径不存在：%s\n'
                       '  路径填错，或素材库还没从团队共享盘拷贝到本机。' % raw}
    videos, images = count_media(raw)
    if videos == 0:
        extra = ('（发现 %d 张图片，但一个视频也没有）' % images) if images else ''
        return {'ok': False, 'code': 'EMPTY', 'path': raw, 'videos': 0, 'images': images,
                'msg': '素材库里没有一个可用的视频文件%s：%s\n'
                       '  继续生成会得到一条「成功但没有画面」的草稿。\n'
                       '  请先整份拷贝素材库；确实要做无画面调试稿，'
                       '请显式打开「允许生成无画面调试稿」（命令行 --allow-empty-materials）。'
                       % (extra, raw)}
    return {'ok': True, 'code': 'OK', 'path': raw, 'videos': videos, 'images': images,
            'msg': '素材库就绪：%d 个视频 / %d 张图片' % (videos, images)}


# ---- 图片可用性下限（2026-09-15 图片入池时定）----
MIN_IMAGE_SIDE = 540        # 短边下限：再小铺满画布会糊
MAX_IMAGE_SIDE = 12000      # 单边上限：多半是坏图/异常头
MAX_IMAGE_RATIO = 4.0       # 长宽比上限：画布是 16:9，极端长条没意义
_IMG_SIZE_CACHE = {}


def image_ok(path):
    """图片能否进池：尺寸在合理区间内。探测不到尺寸 → 不进池。"""
    try:
        key = (os.path.abspath(path), int(os.path.getmtime(path)))
    except OSError:
        return False
    if key in _IMG_SIZE_CACHE:
        return _IMG_SIZE_CACHE[key]
    ok = False
    try:
        # 函数级 import：draft_builder 依赖本模块，顶层 import 会成环
        from draft_builder import probe_media_size
        wh = probe_media_size(path)
        if wh:
            w, h = wh
            ok = (min(w, h) >= MIN_IMAGE_SIDE and max(w, h) <= MAX_IMAGE_SIDE
                  and max(w, h) / max(1, min(w, h)) <= MAX_IMAGE_RATIO)
    except Exception:
        ok = False
    _IMG_SIZE_CACHE[key] = ok
    return ok


def _bucket_of(text):
    """核心匹配：一段文本按 BUCKET_ORDER（具体→通用）命中哪个语义桶。"""
    low = (text or '').lower()
    for bucket in BUCKET_ORDER:
        for kw in SEMANTIC_BUCKETS[bucket]:
            if kw.lower() in low:
                return bucket
    return None


def classify(filename):
    """旧接口：按文件名判断素材语义类别，返回 bucket key 或 None。

    （仅按文件名匹配。2026-09-04 起新素材库语义在**目录名**，
    走 classify_with_dir；此函数保留兼容旧命名库 <语义>_<横竖>.mp4。）
    """
    return _bucket_of(filename)


def classify_with_dir(dir_chain, filename=None):
    """按「目录链」判断素材语义（2026-09-14 起**只用目录命中**）。

    用户素材库结构：E:/园区素材/<园区>/<语义文件夹>[/横屏|竖屏]/DJI_xxx.mp4
    —— 语义写在**文件夹名**上（停车场/厂房内部/大门口/宿舍/公路…），
    文件名多数是无意义的 DJI 时间戳。规则：

    1. 目录链从**最具体的目录（叶子）向上**命中，避免父目录"新疆园区"含"园区"
       把子目录"厂房内部"误归到 environment
    2. **园区名层级不参与判定**：目录名以「园区 / 产业园」结尾的一律跳过
       （"新疆园区""两河园区"里的"园区"会命中 environment 关键词）
    3. **目录没写语义 → 进未分类池（哪里缺补哪里），不再拿文件名猜**

    ⚠️ 为什么取消文件名兜底（2026-09-14 用户拍板）：
    文件名兜底会制造"看起来对、其实错"的归类，且极难发现 —— 实测 118 条被判成
    「航拍」的素材里 103 条是文件名 `DJI_*` 判的，北京那 92 条其实全躺在
    `医械城/专业设施/` 目录里（专业设施 ≠ 航拍）。目录是人工分的、可靠；
    文件名是相机自动生成的、不可靠。

    注：`filename` 仅为兼容既有调用签名保留，**不再参与判定**；
    横竖屏方向仍会看文件名里的「横版/竖版」（见 _orientation_of）。
    """
    for d in reversed(dir_chain or []):
        if d.endswith('园区') or d.endswith('产业园'):
            continue          # 园区名层级：如「新疆园区」「两河园区」，不参与语义判定
        b = _bucket_of(d)
        if b:
            return b
    return None


# 分配期剔除的过短素材阈值：真实时长 < 0.6s 的素材，即使 0.5x 放慢也撑不满
# 最短字幕（1.2s），构建期 fit_clip_speed 会 ok=False 硬撑导致 source 越界。
MIN_MATERIAL_US = 600_000

# 段落级图片约束（2026-09-15 用户拍板）：
#   ① 段落总时长 > 5s → 优先视频素材（静态图铺 5s 太闷，观众会划走）
#   ② 同一段落最多用 2 个图片素材（图片是静帧，堆多了像 PPT 翻页）
LONG_SEG_US = 5_000_000       # 段落（语义块）时长阈值：超过则优先视频
MAX_IMG_PER_BLOCK = 2         # 单段落图片素材上限


# 2026-09-04 起 bucket 内改为「随机抽取」（用户拍板）：
# 每个 bucket 组 shuffle 一次后轮转取用 —— 既有随机性（每次成片不同），
# 又避免 pick_from_pool 邻近重复。原 _score（横版+100/竖版-50/名字长加分）
# 与 _bucket_order_key（整体外观在前）不再参与排序，已删除。
# 横屏/竖屏素材现在都进同一随机池，靠 9:16 画布 scale 放大裁切适配；
# 若之后想"画布方向优先"，在 shuffle 后按 orientation 稳定排序即可。


def parse_semantic_points(subtitles):
    """（旧接口，保留兼容）从字幕文本提取语义点，返回 list of bucket 或 None。"""
    return [_bucket_of(s['text']) for s in subtitles]


# ---------- 语义块规则（2026-09-04 用户带教，2026-09-07 落地代码） ----------
#
# 核心：切分单位 = **语义块**（不是句子/标点），一块 = 一段素材。
#   1. 情绪钩子（"真没想到！""有的，老板！""是的！"）并入相邻块，不单独切
#   2. 排比（不是…不是…更不是…）＝ 一块
#   3. 问句（？）处切
#   4. 相邻同类型块**也用不同画面**（连续多个"厂房内部"块各取不同素材）
#   5. 过短字幕（≤5 字）并入前一块（"可整租""可分租"不构成独立语义）
#
# 块类型 → 素材 bucket 候选（按优先级，第一个没有往下退）：
#   opening   开场（钩子 / 报园区名 / 设问+回答）  entrance → aerial → factory
#   exterior  全新/高标准/国资/多栋/万方/环评/污水处理      factory
#   interior  户型/起租面积/层高/承重/航车行车/水电供暖/消防  inside
#   location  地理位置（长江/合肥/地铁/港口/高速/高铁/铁路）  highway→railway→metro→traffic
#   support   配套（办公/食堂/宿舍/车位/充电桩/生活所需）      dormitory→canteen→office→environment
#   price     租金价格                factory（次选 inside）
#   policy    入驻 / 扶持政策          factory
#   cta       点击链接 / 预约考察       factory
#
# 同块内多词冲突时按此优先级判定主语义（用户 5 条脚本反推）：
#   cta > policy > price > location > support > interior > exterior
# 块类型 → 素材桶候选（按优先级，第一个没素材就往下退）。
# 内容见 config/semantics.json 的 block_buckets，注释版含义：
#   opening 开场（钩子/报园区名/设问+回答）entrance→aerial→factory
#   exterior 全新/高标/国资/多栋/万方/环评  factory
#   interior_area 户型/面积/起租 · interior_struct 层高/承重/行车
#   interior_fac 水电/供暖/消防 · location 交通 · support 宿舍→食堂→办公→配套
#   price 租金价格（次选 inside） · policy 扶持政策 · cta 点击链接
# 'interior' / 'interior_spec' 为兼容旧名保留。
BLOCK_BUCKETS = _S['block_buckets']

# ---------------------------------------------------------------------------
# 交通类型 → bucket（2026-09-09 用户拍板）
# 旧逻辑 location 块用固定优先级 highway→railway→metro→traffic，
# 于是「城际轻轨直达杭州」被配到「高速-竖版」素材 —— 文案讲轻轨、画面给高速。
# 改为按块内实际命中的交通词决定先取哪个桶。
# ---------------------------------------------------------------------------
TRAFFIC_BUCKET_HINTS = _S['traffic_bucket_hints']   # 内容见 config/semantics.json


def location_buckets(text):
    """location 块按句内实际命中的交通词排序 bucket；未命中则回退固定优先级。"""
    base = BLOCK_BUCKETS.get('location', ['railway', 'metro', 'traffic'])
    ordered = []
    for kws, bk in TRAFFIC_BUCKET_HINTS:
        if any(k in (text or '') for k in kws) and bk not in ordered:
            ordered.append(bk)
    for bk in base:
        if bk not in ordered:
            ordered.append(bk)
    return ordered


# ---------------------------------------------------------------------------
# support 块同样改为「按句内实际词动态排序」（2026-09-14 用户拍板，P3）
# 旧逻辑固定 dormitory→canteen→office→environment，于是「生活配套完善」一律
# 配到宿舍素材；而全库 office 只有 2 条、environment 10 条，等于宿舍一枝独秀。
# 现在：句子里提到什么就先取什么（食堂→canteen、办公→office、环境/配套→environment），
# 都没命中才回退到固定顺序（仍以宿舍打头，保持旧行为）。
# ---------------------------------------------------------------------------
SUPPORT_BUCKET_HINTS = _S.get('support_bucket_hints', [])


def support_buckets(text):
    """support 块按句内实际词排序 bucket；未命中则回退固定优先级。"""
    base = BLOCK_BUCKETS.get('support', ['dormitory', 'canteen', 'office', 'environment'])
    ordered = []
    for kws, bk in SUPPORT_BUCKET_HINTS:
        if any(k in (text or '') for k in kws) and bk not in ordered:
            ordered.append(bk)
    for bk in base:
        if bk not in ordered:
            ordered.append(bk)
    return ordered


# 不可跨园区借用的桶（2026-09-09 用户拍板）：
# 厂房外部是各园区自己的门面，混用一眼就能看出不是同一个园区。
NO_CROSS_BUCKETS = _S['no_cross_buckets']   # 内容见 config/semantics.json

_CROSS_POOL_CACHE = {}


def cross_park_pool(materials_dir, exclude_buckets=NO_CROSS_BUCKETS, cur_park=None):
    """当前园区缺某个语义桶时，从**兄弟园区**借同桶素材（兜底用）。

    2026-09-11 起改为**规则驱动**（规则来自 `语义规则分配表.xlsx` → config/park_rules.json）：
    0. 只有表里标记为"可跨园区通用"的桶才许借（默认只有 inside / dormitory）；
       没标记的桶一律不借 —— 宁可用本园区素材反复用，也不混入别园区画面。
    1. 厂房外部 factory 是园区特有，绝不跨园区交叉使用（NO_CROSS_BUCKETS 硬红线，规则也改不动）
    2. 某桶标记了"通用例外"的园区整体排除（如 dormitory 排除新疆园区）
    3. 文件**名**里出现"特殊地名"的素材不可混用
       —— 例：贵阳的「贵阳绕城高速和沪昆高速匝道立交」不能借给嘉兴脚本，
          观众认得出那是贵阳。判定只看文件名、不看整条路径：素材的上层目录
          本来就叫「贵阳园区」，拿整条路径去判会把该园区素材全部误杀。

    园区名与地名词从素材库根目录的子目录名自动提取（嘉兴园区 → 嘉兴），
    不维护硬编码清单，以后新增园区无需改代码。

    结果按 (materials_dir, cur_park) 缓存：扫描兄弟园区要探测几百个文件时长。
    """
    key = (os.path.normpath(os.path.abspath(materials_dir or '')), cur_park or '')
    if key in _CROSS_POOL_CACHE:
        return _CROSS_POOL_CACHE[key]
    # 规则允许借的桶（无 park_rules 时退回旧行为：全部可借，仅 NO_CROSS_BUCKETS 除外）
    allowed = _pr.cross_buckets() if _pr else None
    pool = {}
    try:
        cur_abs = os.path.abspath(materials_dir or '')
        root = os.path.dirname(cur_abs)
        cur_name = os.path.basename(cur_abs.rstrip('/\\'))
        if os.path.isdir(root):
            names = [d for d in sorted(os.listdir(root))
                     if os.path.isdir(os.path.join(root, d))]
            place_words = {}
            for d in names:
                w = d.replace('园区', '').replace('产业园', '').strip()
                if len(w) >= 2:
                    place_words[d] = w
            for d in names:
                if d == cur_name:
                    continue
                try:
                    items = _scan_one_dir(os.path.join(root, d))
                except Exception:
                    continue
                for it in items:
                    b = it.get('bucket')
                    if not b or b in exclude_buckets:
                        continue
                    if allowed is not None and b not in allowed:
                        continue          # 规则未开放该桶 → 不借
                    if _pr and d in _pr.excluded_parks(b):
                        continue          # 该桶的通用例外里点名排除了这个园区
                    fn = os.path.basename(it.get('path') or '')
                    # 文件名里带别的园区地名的素材一律不借
                    if any(w in fn for od, w in place_words.items() if od != cur_name):
                        continue
                    pool.setdefault(b, []).append(it)
    except Exception:
        pass
    # 邻近园区优先（2026-09-14 用户拍板）：允许借用的桶里，
    # 先从"邻近园区"取（表里人工维护，如 嘉定↔苏州/嘉兴/新仓），取完再取远处的。
    # 目的不是放宽规则，而是**降低穿帮概率** —— 长三角的宿舍楼彼此更像。
    near = set(_pr.nearby_parks(cur_park)) if (_pr and cur_park) else set()
    for k in pool:
        if near:
            near_items = [it for it in pool[k] if _park_of_path(it.get('path'), near)]
            far_items = [it for it in pool[k] if it not in near_items]
            random.shuffle(near_items)
            random.shuffle(far_items)
            pool[k] = near_items + far_items
        else:
            random.shuffle(pool[k])
    _CROSS_POOL_CACHE[key] = pool
    return pool


def _park_of_path(path, candidates):
    """这条素材是否落在 candidates 里的某个园区目录下。"""
    norm = (path or '').replace('\\', '/')
    for d in candidates:
        if '/' + d + '/' in norm:
            return True
    return False

CTA_KWS = _S['cta']
POLICY_KWS = _S['policy']
PRICE_KWS = _S['price']
# 居住实体词（2026-09-16 用户拍板，方案 A）：必须**排在价格词之前**判定。
# 反例（新仓/嘉定脚本）：「精装人才公寓月租500元起」—— "500元"命中价格词「元」，
# 整句被拖进价格块（桶 factory→inside），于是画面配到厂房素材、永远不查宿舍桶。
# 居住实体词（宿舍/公寓/住宿）才是画面语义的主体，价格只是它的修饰，故优先。
RESIDENCE_KWS = _S['residence']
# 地理位置判定：用「位置信号词」而非罗列城市名。
# 城市/园区名是开放式词汇，逐个补充不可持续；改用位置信号词后，
# 任意「X边上 / 位于X / 紧邻X」都自动命中，未来新增城市无需再改代码。
LOCATION_MARKERS = _S['location_markers']
# 封闭类交通/地标词（不随园区变化，稳定可保留）
LOCATION_KWS = _S['location']
# '配套' 太宽（"园区厂房及配套楼共6万方"是规模句，应判 exterior），
# 收窄成"配套完善/生活配套/配套设施"才判 support
SUPPORT_KWS = _S['support']
# interior 细分三档（用户案例反推）：
#   area   户型/面积/起租（"超多户型可选可整租可分租"、"1000平起租"、"500㎡起租"）
#   struct 层高/承重/行车/航车/生产仓储（"层高4.5-10.8米，一楼可配10吨行车"）
#   fac    水电/供暖/蒸汽/消防/满足需求（"丙类消防，满足…需求"、"水电齐全供暖"）
# 苏州/嘉定用户明确要把 area 与 struct 分成两块；乌鲁木齐"层高+做生产做仓储"同属 struct 一块。
INTERIOR_AREA_KWS = _S['interior_area']
# 注意 '方' 不入 area——"15万方/6万方"是厂房规模，用户定为厂房外部
INTERIOR_STRUCT_KWS = _S['interior_struct']
INTERIOR_FAC_KWS = _S['interior_fac']
# 基础设施硬关键词：水电/蒸汽/供暖/消防/丙类 必须优先于 support，
# 否则"水电蒸汽配套完善"会被 support 的"配套完善"吞掉 → 归到宿舍素材
# 注意 '需求' 不放这里——"满足…需求"属配套(support)，要排在 support 之后
INTERIOR_FAC_STRICT_KWS = _S['interior_fac_strict']
INTERIOR_KWS = _S['interior']
EXTERIOR_KWS = _S['exterior']
SHORT_EXTERIOR_LEN = _S['short_exterior_len']
# HOOK_KWS：已移除（定义后从未参与判定），勿再依赖
PARK_KWS = _S['park']
# SHORT_SUB_LEN：已移除（定义后从未参与判定），勿再依赖


def tag_subtitle(text):
    """单条/单块文本 → 语义块类型（细类型，用于分块与 bucket 查找）。

    优先级：cta > policy > residence > price > location > support > interior_fac > interior_spec > exterior
    residence = 居住实体词（宿舍/公寓/住宿，2026-09-16 方案 A）：先于价格判定 ——
    否则「精装人才公寓月租500元起」会被价格词「元」拖进价格块 → 配到厂房素材。
    interior 细分成 spec（规格）/ fac（设施资质）：嘉定"层高承重行车"与"丙类消防+满足需求"
    用户明确要分成两块，而乌鲁木齐"层高承重+做生产做仓储"要合一块。
    """
    t = text or ''
    if any(k in t for k in CTA_KWS):
        return 'cta'
    if any(k in t for k in POLICY_KWS):
        return 'policy'
    # 居住实体词优先于价格（2026-09-16 用户拍板，方案 A）：
    # "精装人才公寓月租500元起"里的"500元"命中价格词，若不先判居住词，
    # 整句会归到价格块（桶 factory→inside），永远配不到宿舍素材。
    if any(k in t for k in RESIDENCE_KWS):
        return 'support'
    if any(k in t for k in PRICE_KWS):
        return 'price'
    if any(k in t for k in LOCATION_KWS):
        return 'location'
    # 基础设施硬关键词（水电/蒸汽/供暖/消防/丙类）优先于 support：
    # 否则"水电蒸汽配套完善"会被 support 的"配套完善"吞掉 → 归到宿舍素材
    if any(k in t for k in INTERIOR_FAC_STRICT_KWS):
        return 'interior_fac'
    if any(k in t for k in SUPPORT_KWS):
        return 'support'
    # 仅剩 '需求'：必须排在 support 之后（"满足…需求"属配套 support）
    if any(k in t for k in INTERIOR_FAC_KWS):
        return 'interior_fac'
    if any(k in t for k in INTERIOR_AREA_KWS):
        return 'interior_area'
    if any(k in t for k in INTERIOR_STRUCT_KWS):
        # 生产后跟厂房 → 厂房外部（建筑类型）；无厂房(如"做生产做仓储")→内部
        if '生产' in t and '厂房' in t[t.find('生产'):]:
            return 'exterior'
        return 'interior_struct'
    if any(k in t for k in EXTERIOR_KWS):
        return 'exterior'
    return 'exterior'


def _is_fallback(text):
    """兜底判定：没有任何关键词命中（如"都非常合适"这类收尾短语）。"""
    t = text or ''
    all_kw = (CTA_KWS + POLICY_KWS + PRICE_KWS + LOCATION_KWS + SUPPORT_KWS
              + INTERIOR_KWS + EXTERIOR_KWS)
    return not any(k in t for k in all_kw)


def _opening_range(texts):
    """开场块范围（返回结束索引）。

    规则（用户 5 条脚本反推）：
    - 优先：前 4 条里找第一条**含园区名**的字幕 → 开场块到该条为止
      （乌鲁木齐"租金3毛钱？…只要3毛钱？是的！乌鲁木齐两河产业园"整体是开场）
    - 无园区名：在问句（？）处切（苏州三连否"…你了解过吗？"自成一开场块）
    - 都没有：取前 3 条（钩子 + 紧随卖点连击，如"真没想到！…还能享超长免租期！"）
    """
    n = len(texts)
    for j in range(min(n, 4)):
        if any(k in texts[j] for k in PARK_KWS):
            return j
    for j in range(min(n, 4)):
        if texts[j].rstrip().endswith(('？', '?')):
            return j
    # 兜底取前 3 条，但**不能吞掉 CTA**（极短脚本里"点击链接"可能落在前 3 条内）
    end = min(n - 1, 2)
    while end > 0 and any(k in texts[end] for k in CTA_KWS):
        end -= 1
    return max(end, 0)


def split_semantic_blocks(subtitles):
    """字幕序列 → 语义块列表 [{'idxs':[字幕索引], 'type':块类型}]。

    每块对应一段素材；相邻同类型块仍是不同块（各取不同画面），
    但**连续同细类型的字幕合并成一块**（如"层高"+"行车"、"配套"三句）。
    """
    texts = [s['text'] if isinstance(s, dict) else str(s) for s in subtitles]
    n = len(texts)
    if not n:
        return []
    tags = [tag_subtitle(t) for t in texts]

    # 预合并：短外部句（≤6 字，如"高规格厂房"）紧随内部句时并入该内部块
    # （用户乌鲁木齐案例："高规格厂房，500㎡起租"整体归厂房内部）
    for k in range(n - 1):
        if (tags[k] == 'exterior' and len(texts[k].strip()) <= SHORT_EXTERIOR_LEN
                and tags[k + 1].startswith('interior')):
            # 用户规则#1：生产厂房 → 外部，不被短外部并入规则吞掉（保持独立外部块）
            if '生产' in texts[k] and '厂房' in texts[k][texts[k].find('生产'):]:
                continue
            tags[k] = tags[k + 1]

    open_end = _opening_range(texts)
    blocks = [{'idxs': list(range(0, open_end + 1)), 'type': 'opening'}]
    i = open_end + 1

    # 有效语义前向传播：无关键词的 fallback 句沿用前一条“有语义”句的类型，
    # 避免无关键词句一律回退成 exterior 造成错配（如“给员工发福利”应并入前 price 块、
    # 公寓段应并入前 support 块）。命中关键词的句（含位置/公寓等）已是非 fallback，
    # 不受影响——“而且位置也很好”靠 LOCATION_MARKERS 命中 location，不会被吸进 price。
    eff = list(tags)
    for i2 in range(1, n):
        if _is_fallback(texts[i2]):
            eff[i2] = eff[i2 - 1]

    while i < n:
        t, tg = texts[i], eff[i]
        # 1) CTA 触发后到结尾整体一块（不并入前块，即使前块也是外部素材）
        if tg == 'cta':
            idxs = list(range(i, n))
            blocks.append({'idxs': idxs, 'type': 'cta'})
            i = n
            continue
        # 2) 排比（不是…不是…更不是…）合并成一块
        if '不是' in t:
            idxs = [i]
            j = i + 1
            while j < n and '不是' in texts[j]:
                idxs.append(j)
                j += 1
            blocks.append({'idxs': idxs, 'type': tag_subtitle(''.join(texts[k] for k in idxs))})
            i = j
            continue
        # 3) 无关键词的 fallback 句直接并入前一块（沿用前块有效语义），
        #    避免一律回退 exterior 造成错配（如“给员工发福利”并入 price、
        #    公寓段并入 support）；且它不发起新合并，保留相邻同类型块独立性
        #    （乌鲁木齐“大环评+污水处理”仍独立成外部块，与“33栋厂房”各取画面）
        if _is_fallback(t):
            if blocks:
                blocks[-1]['idxs'].append(i)
            else:
                blocks.append({'idxs': [i], 'type': tg})
            i += 1
            continue
        # 4) 有语义句开启新块，连续合并后续同类型且非 fallback 的句
        #    （层高+行车、配套三句、租金+免租期；遇 fallback 句即停，不吞并）
        idxs = [i]
        j = i + 1
        while j < n and eff[j] == tg and eff[j] != 'cta' and not _is_fallback(texts[j]):
            idxs.append(j)
            j += 1
        blocks.append({'idxs': idxs, 'type': tg})
        i = j

    # price 块紧跟 exterior 块时并入前块（嘉定"…高品质厂房，现在咨询即享预招商租金优惠"
    # 用户明确并入前一块）；紧跟 opening/support 时保持独立（淮南/苏州案例）
    merged = []
    for b in blocks:
        if b['type'] == 'price' and merged and merged[-1]['type'] == 'exterior':
            merged[-1]['idxs'].extend(b['idxs'])
        else:
            merged.append(b)
    return merged


def parse_semantic_classes(subtitles):
    """（兼容旧接口）字幕 → [(块类型, bucket)]，按语义块展开到每条字幕。"""
    out = [None] * len(subtitles)
    for b in split_semantic_blocks(subtitles):
        buckets = BLOCK_BUCKETS.get(b['type'], ['factory'])
        for idx in b['idxs']:
            out[idx] = (b['type'], buckets[0])
    return [(x or ('exterior', 'factory')) for x in out]


def match_materials(subtitles, materials_dir, out_paths=False, max_run=5,
                    sub_durations=None, min_slow=0.5, park=None, seed=None,
                    video_orientation=None, script_text=None,
                    record_usage=True, usage_path=None):
    """为字幕序列匹配素材，返回 list of dict {path, bucket, duration}，长度=字幕数。

    对齐模板：9~10 段素材覆盖 17 条字幕（每段覆盖 1~3 条）。
    规则：
    1. 语义点变化时优先切换对应 bucket 素材
    2. 同一素材最多连续 max_run 条字幕
    3. **容量约束**：一个素材能撑的最长展示时长 = 素材时长 / min_slow
       （放慢到 min_slow 倍速）。超过就提前换素材，避免出现定格画面
    4. 2026-09-04 起 bucket 内**随机抽取**（用户拍板）：每组 shuffle 一次后轮转，
       随机但不邻近重复
    5. 无语义点(None)的字幕并入前一段素材
    6. 素材不足时回退到未分类素材，再回退到当前素材
    7. 真实时长 < MIN_MATERIAL_US 的过短素材分配期剔除（0.5x 也撑不满最短字幕）
    8. **横竖屏隔离**（用户拍板 2026-09-04）：带 横屏/横版/竖屏/竖版 标注的素材
       只能用于同方向视频；**无标注的通用**。video_orientation=None 表示用户
       未指定方向 → 不过滤（兼容旧行为）

    sub_durations：每条字幕的展示时长（微秒）。传了才启用容量约束。
    seed：随机种子（None=真随机；传数字可复现同一批素材，回归/调试用）。
    video_orientation：'portrait'/'landscape'/None。每次剪辑由用户提前告知。
    script_text：脚本文本，用于自动识别园区地名（2026-09-11 新增）。

    ⚠️ 2026-09-11 起：**先锁定园区，再选素材**。
    materials_dir 传素材总根（下面挂着多个园区文件夹）时，必须能解析出园区
    （--park > 脚本地名 > 目录名）；解析不出来直接抛错，不再退化成"多园区大混池"
    —— 贵阳片就是混池把 41 条贵阳素材稀释到 4.6%，7 段画面全被别园区抽走。
    """
    park_dir, park_folder, how = (None, None, '')
    if _pr:
        park_dir, park_folder, how = _pr.resolve_park_dir(materials_dir, park, script_text)
        if park_dir:
            if os.path.normpath(park_dir) != os.path.normpath(os.path.abspath(materials_dir or '')):
                print('[园区锁定] %s → %s（依据：%s）' % (materials_dir, park_folder, how))
            materials_dir = park_dir
        elif _pr.looks_like_root(materials_dir):
            raise ValueError(
                '素材目录 %s 下挂着多个园区，但%s，无法确定用哪个园区的素材。\n'
                '  请二选一：① 在脚本里写明园区地名；② 命令行加 --park <园区名>；'
                '③ 直接把 --materials 指到具体园区目录。\n'
                '  （已拦停，避免多园区素材混用）' % (materials_dir, how or '未识别到园区'))
    items = scan_materials(materials_dir, park=park)
    if not items:
        # 无素材目录：返回空占位（draft_builder 生成空段）
        return [None] * len(subtitles)

    # 横竖屏隔离：方向不符的素材直接出局（即使语义再贴也不用），无标注素材不受限。
    # 2026-11-02：orientation 已由 _resolve_orientation 按真实画面校正，冲突在此汇总提示。
    _print_orientation_conflicts()
    all_items = list(items)                 # 方向回退用（方案 B，见下）
    if video_orientation:
        before = len(items)
        items = [it for it in items
                 if it.get('orientation') in (None, video_orientation)]
        print('[方向过滤] %s: %d -> %d 个素材（剔除反向 %d 个）'
              % (video_orientation, before, len(items), before - len(items)))

    rng = random.Random(seed)
    all_items = [it for it in all_items
                 if not (it.get('duration') and it['duration'] < MIN_MATERIAL_US)]
    items = [it for it in items
             if not (it.get('duration') and it['duration'] < MIN_MATERIAL_US)]
    by_bucket = {}
    for it in items:
        by_bucket.setdefault(it['bucket'], []).append(it)

    # ---------- 方案 B（2026-09-15 用户拍板）：整桶同方向为 0 → 回退另一方向 ----------
    # 「横竖屏隔离」的初衷是"别把竖屏素材塞进横版成片"；但某个语义桶在本园区
    # **一条同方向素材都没有** 时，隔离的代价是该块完全换不了画面（只能沿用上一段，
    # 实测嘉定竖版 60 条只剩 3 条、画面长时间不换）。此时退回该桶的另一方向素材——
    # 剪映反正要按画布比例裁切，裁切效果由用户在剪映自行判断。
    # ⚠️ 只在"整桶为空"时触发，同方向有素材的桶行为完全不变。
    if video_orientation:
        # 先按桶收集"整桶为空"的反向素材，再整桶写入 —— 不能边写边判
        # （写入首个后 `b not in by_bucket` 即为假，只会加进 1 条）。
        fb = {}
        for it in all_items:
            if it.get('orientation') in (None, video_orientation):
                continue
            b = it.get('bucket')
            if b is not None and b not in by_bucket:
                fb.setdefault(b, []).append(it)
        for b, lst in fb.items():
            by_bucket[b] = lst
            print('[方向回退] 桶 %s：本园区该桶无 %s 素材，回退用另一方向 %d 条'
                  % (b, video_orientation, len(lst)))
        # 未分类池同理（bucket=None 的素材走 unused，不在 by_bucket 里查）
        if not any(it['bucket'] is None for it in items):
            fb_unused = [it for it in all_items if it['bucket'] is None]
            if fb_unused:
                items = items + fb_unused
                print('[方向回退] 未分类池：无 %s 素材，回退用另一方向 %d 条'
                      % (video_orientation, len(fb_unused)))

    for k in by_bucket:
        rng.shuffle(by_bucket[k])
    unused = [it for it in items if it['bucket'] is None]
    rng.shuffle(unused)

    # ---------- 按语义块分配素材（2026-09-07 落地用户语义块规则） ----------
    blocks = split_semantic_blocks(subtitles)
    matched = [None] * len(subtitles)
    prev_item = None

    # ---------- 素材治理（P1-①）：大库启用使用频次降权 ----------
    # 只在素材总数达标时启用：小库本来就没得选，再降权会把可选范围压到个位数，
    # 成片反而更单调甚至凑不出素材（2026-09-08 贵阳仅 41 条实测得出的教训）。
    # seed 模式同样启用：seed 固定了 shuffle 顺序、usage 是确定输入，
    # 两者组合后仍可复现（同一 usage 状态下跑两次结果一致），便于调试观察。
    try:
        import material_governance as _mg
    except Exception:
        _mg = None
    usage = _mg.load_usage(usage_path) if _mg else {}
    _DW_WINDOW = _mg.DOWNWEIGHT_WINDOW if _mg else 8
    # 降权按**池**判断（不是按整个素材库）：池内素材够多才降权。
    # 按库判断要么永不生效、要么误伤只有几个素材的小池，见 material_governance 说明。
    dw_min = _mg.DOWNWEIGHT_MIN_POOL if _mg else 10 ** 9

    pool_ptr = {}   # 每个素材池独立的轮转游标（见下方"轮转取用"注释）

    def _orient_filter(lst):
        """跨园区借来的素材同样要过方向过滤。

        方案 B（2026-09-15）：整池同方向为 0 时回退整池（借来的桶也适用），
        避免"借来了却因为方向又被清空" → 该块只能沿用上一段画面。
        """
        if not video_orientation:
            return lst
        matched = [it for it in lst
                   if it.get('orientation') in (None, video_orientation)]
        return matched or lst

    def pool_for(btype, text=''):
        """按块类型的 bucket 优先级找第一个有素材的池（没有就降级到下一个）。

        2026-09-09 两处增强：
        1. location 块按块内实际命中的交通词动态排序桶（轻轨→metro 而非公路）
           support 块同理（食堂→canteen、办公→office、配套→environment）
        2. 本园区全部落空后，从兄弟园区借同桶素材（factory 等园区特有桶除外）
        """
        bks = (location_buckets(text) if btype == 'location'
               else support_buckets(text) if btype == 'support'
               else BLOCK_BUCKETS.get(btype, ['factory']))
        for bk in bks:
            p = by_bucket.get(bk)
            if p:
                return p
        # 本园区没有 → 跨园区借用（仅限规则开放的桶，见 park_rules）
        cross = cross_park_pool(materials_dir, cur_park=park_folder)
        if cross:
            for bk in bks:
                if bk in NO_CROSS_BUCKETS:
                    continue
                p = cross.get(bk)
                if p:
                    return _orient_filter(p)
        return unused

    def fit_ok(item, need_us):
        """素材放慢到 min_slow 能否撑住 need_us。"""
        cap = (item.get('duration') or 0) / min_slow
        return cap <= 0 or cap >= need_us

    for b in blocks:
        idxs = b['idxs']
        blk_text = ''.join((subtitles[i].get('text') or '') for i in idxs)
        pool = pool_for(b['type'], blk_text)
        if not pool:
            for i in idxs:
                matched[i] = prev_item
            continue
        # 块内容量分段：单素材撑不住整块就按字幕拆开（用户"素材不够就换素材"）
        blk_dur = sum(sub_durations[i] for i in idxs) if sub_durations else 0
        # 段落级图片约束（2026-09-15 用户拍板）：
        #   ① 段落总时长 > 5s → 优先视频素材（静态图铺 5s 太闷）
        #   ② 同一段落最多用 2 个图片素材（图片是静帧，堆多了像 PPT）
        block_prefers_video = bool(sub_durations) and blk_dur > LONG_SEG_US
        block_img_left = MAX_IMG_PER_BLOCK
        best_cap = max(((it.get('duration') or 0) / min_slow) for it in pool)
        if blk_dur > best_cap and len(idxs) > 1:
            groups = [[i] for i in idxs]
        else:
            groups = [idxs]
        for grp in groups:
            need = sum(sub_durations[i] for i in grp) if sub_durations else 0
            # 本组也优先视频：整段 >5s，或该组自身就 >5s（容量拆分后才可能）
            prefer_video = block_prefers_video or need > LONG_SEG_US
            item = None
            # 轮转取用：每个池记住上次取到哪，下次从下一个接着找。
            # 旧实现每块都从 pool[0] 开始扫，于是永远是"跳过刚用过的那个、
            # 再拿池里第一个能撑住的" —— 单条成片只在 pool 前两个素材之间来回切，
            # 池里其余素材一次都用不到。
            # 贵阳实测：厂房外部横屏有 9 个素材，7 个语义块却只用了 2 个来回出现。
            key = id(pool)
            n = len(pool)
            start = pool_ptr.get(key, 0)

            def _cand_ok(cand):
                """容量达标 + 段落图片约束：优先视频 / 图片额度用尽则跳过图片。"""
                if cand.get('kind') == 'image' and (prefer_video or block_img_left <= 0):
                    return False
                return fit_ok(cand, need)

            if len(pool) >= dw_min:
                # 降权：先在窗口内收集所有可用候选，再挑用得最少的。
                # 仍从游标 start 开始扫，因此"不邻近重复"的轮转行为保持不变 ——
                # 降权只在窗口内部决定取哪一个，不推翻轮转。
                cands = []
                for off in range(n):
                    cand = pool[(start + off) % n]
                    if prev_item is not None and cand['path'] == prev_item['path'] \
                            and len(pool) > 1:
                        continue
                    if _cand_ok(cand):
                        cands.append((off, cand))
                    if len(cands) >= _DW_WINDOW:
                        break
                if cands:
                    pos, item = _mg.pick_least_used([c for _, c in cands],
                                                    usage, return_pos=True)
                    pool_ptr[key] = (cands[pos][0] + 1) % n
            else:
                for off in range(n):
                    cand = pool[(start + off) % n]
                    if prev_item is not None and cand['path'] == prev_item['path'] \
                            and len(pool) > 1:
                        continue
                    if _cand_ok(cand):
                        item = cand
                        pool_ptr[key] = (start + off + 1) % n
                        break
            if item is None:
                # 都撑不住 / 全被图片约束滤掉：尽量挑视频，退而求其次用任意素材
                vids = [it for it in pool if it.get('kind') == 'video']
                item = (max(vids, key=lambda it: it.get('duration') or 0)
                        if vids else max(pool, key=lambda it: it.get('duration') or 0))
                pool_ptr[key] = (start + 1) % n
            # 记录图片占用（段落级额度，跨组内小组共享）
            if item is not None and item.get('kind') == 'image':
                block_img_left -= 1
            for i in grp:
                matched[i] = item
            prev_item = item

    # 首尾空洞回填：开头/结尾没有素材的字幕并入最近的素材，保证视频轨覆盖整条时间线
    first = next((m for m in matched if m is not None), None)
    if first is not None:
        for i in range(len(matched)):
            if matched[i] is None:
                matched[i] = first
            else:
                break
        for i in range(len(matched) - 1, -1, -1):
            if matched[i] is None:
                matched[i] = matched[i - 1] if i > 0 else first
            else:
                break

    # 记录本次用到的素材（P1-①）：供下次降权，以及"哪些素材从没露过面"统计
    if _mg and record_usage:
        try:
            used_paths = []
            for m in matched:
                p = m['path'] if m else None
                if p and p not in used_paths:
                    used_paths.append(p)
            _mg.record_usage_atomic(used_paths, usage_path)
        except Exception as e:
            print('[素材治理] 频次记录失败（不影响出片）：%s' % e)

    if out_paths:
        return [m['path'] if m else None for m in matched]
    return matched


def report_park(root, park):
    """分析某 park 的素材覆盖：每个语义 bucket 是「专属 / 通用 / 缺失」。

    返回 dict：{park, shared_buckets, park_buckets, coverage:[(bucket, src), ...]}。
    src ∈ {'专属','通用','缺失'}。
    """
    folder = resolve_park(root, park) if park else None
    shared_items = _scan_one_dir(os.path.join(root, '_shared'))
    shared_buckets = set(it['bucket'] for it in shared_items if it['bucket'])
    park_items = []
    if folder:
        pd = os.path.join(root, '_parks', folder)
        if os.path.isdir(pd):
            park_items = _scan_one_dir(pd)
    park_buckets = set(it['bucket'] for it in park_items if it['bucket'])
    coverage = []
    for b in BUCKET_ORDER:
        if b in park_buckets:
            src = '专属'
        elif b in shared_buckets:
            src = '通用'
        else:
            src = '缺失'
        coverage.append((b, src))
    return {'park': folder or '(未指定)',
            'shared_buckets': shared_buckets,
            'park_buckets': park_buckets,
            'coverage': coverage,
            'shared_count': len(shared_items),
            'park_count': len(park_items)}


def _init_root(root):
    """创建集中根目录骨架 + 空 _manifest.json。已存在则跳过。"""
    os.makedirs(os.path.join(root, '_shared'), exist_ok=True)
    os.makedirs(os.path.join(root, '_parks'), exist_ok=True)
    manifest = os.path.join(root, '_manifest.json')
    if not os.path.isfile(manifest):
        json.dump({'parks': {}, 'shared_buckets': []},
                  open(manifest, 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=2)
    # 给每个已知 bucket 建占位子目录提示（仅 _shared 下，便于用户知道该放什么）
    for b in BUCKET_ORDER:
        os.makedirs(os.path.join(root, '_shared', '_' + b), exist_ok=True)
    print('已初始化素材根目录:', root)
    print('  _shared/        通用素材（所有园区可用，只存一份）')
    print('  _parks/<园区>/  各园区专属素材')
    print('  _manifest.json  园区别名与通用 bucket 声明')


if __name__ == '__main__':
    import sys
    import argparse
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='素材匹配 / 根目录管理')
    ap.add_argument('script', nargs='?', help='脚本文本文件（用于试匹配）')
    ap.add_argument('--root', help='集中素材根目录（含 _shared/_parks）')
    ap.add_argument('--park', help='园区名（解析 _parks/<名> 或 manifest 别名）')
    ap.add_argument('--materials', help='普通素材目录（单目录模式，兼容旧用法）')
    ap.add_argument('--init', metavar='ROOT', help='初始化集中素材根目录骨架')
    ap.add_argument('--report', action='store_true', help='报告某 park 的素材覆盖情况')
    ap.add_argument('--style', default='3', help='风格键（默认 3）')
    args = ap.parse_args()

    if args.init:
        _init_root(args.init)
        sys.exit(0)

    if args.report:
        if not args.root:
            print('错误：--report 需要 --root')
            sys.exit(1)
        r = report_park(args.root, args.park)
        print(f'园区: {r["park"]}  (通用素材 {r["shared_count"]} 个, 专属素材 {r["park_count"]} 个)')
        print('语义 bucket 覆盖：')
        for b, src in r['coverage']:
            mark = {'专属': '✅', '通用': '🔄', '缺失': '❌'}[src]
            print(f'  {mark} {b:<12} {src}')
        missing = [b for b, s in r['coverage'] if s == '缺失']
        if missing:
            print(f'\n缺失 bucket（无任何素材）: {missing}')
        sys.exit(0)

    # 试匹配模式
    if not args.script:
        print('用法：')
        print('  --init ROOT              初始化集中素材根目录')
        print('  --report --root ROOT [--park 名]   查看覆盖')
        print('  script.txt --root ROOT --park 名   试匹配')
        sys.exit(1)
    subs = json.load(open(os.path.join(os.path.dirname(__file__), '..', 'config',
                                      'default_styles.json'), encoding='utf-8'))['styles'][args.style]
    import subtitle_split
    script = open(args.script, encoding='utf-8').read()
    subs_list = subtitle_split.split_subtitles(script, subs)
    materials_dir = args.root or args.materials
    items = scan_materials(materials_dir, park=args.park)
    print(f'素材目录扫描({materials_dir}, park={args.park}): {len(items)} 个')
    for it in items[:20]:
        print('  ', it['bucket'], '->', it['name'])
    res = match_materials(subs_list, materials_dir, park=args.park)
    print(f'\n素材匹配结果 ({len(res)} 条字幕):')
    for i, (s, m) in enumerate(zip(subs_list, res)):
        print(f'  [{i}] {s["text"][:18]:<20} <- {m["name"] if m else "None"}')
