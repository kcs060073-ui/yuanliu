# -*- coding: utf-8 -*-
"""剪映安装目录探测 —— 全工程单一实现。

为什么单独成一个模块
--------------------
探测逻辑原先在 4 个模块里各抄了一份（make_park_video / arrange_park_video /
replace_seg / draft_builder），四处都只向安装根目录下探**一层**。2026-09-16 实测：
剪映 11.4.2.14459 装在

    %LOCALAPPDATA%\\JianyingPro\\Apps\\11.4.2.14459\\videoeditor.dll

比预期多一层 ``Apps\\``，于是四处探测全部失败 —— 本机明明装了剪映，doctor 却报
「未找到剪映安装目录」，出片直接拦停。抄多份的直接代价就是改一处漏三处，
所以收敛到这里一份，其余模块只做薄封装（各自保留原有的返回约定）。

支持的目录布局
--------------
1) 旧布局：``<base>\\<版本>\\videoeditor.dll``
2) 新布局：``<base>\\Apps\\<版本>\\videoeditor.dll``（剪映新版启动器）

探测优先级：``hint``（命令行显式指定）> 官方安装根目录 > 含目标文件的**最新版本**目录。
新旧布局并存时也按版本号比大小（谁新用谁），不是「浅层命中就算」。
找不到一律返回空串，由调用方决定是抛错还是兜底（各模块原约定不同，不在此处改变）。
"""
import os
import re

import platform_paths as _pp

# 剪映安装根目录候选（按优先级排列）—— 走平台抽象，win32 返回值与历史逐字一致
BASES = _pp.jianying_app_roots()

# 版本目录之上的中间层：旧布局直接是 <base>\<版本>，新布局多一层 Apps\
# （macOS：app 包内的 MacOS / Frameworks）
MID_LEVELS = _pp.app_mid_levels()

# 加解密 native 库名：Windows=videoeditor.dll / macOS=libvideoeditor.dylib
VIDEOEDITOR = _pp.native_lib_name()


def _verkey(name):
    """目录名的版本排序键：数字段优先，取不到数字时按原文排（会排在数字版之后）。

    不能直接用字符串排序 —— 剪映版本号会走到两位数段，
    '11.9.0' 字符串比 '11.10.0' 大，会把旧版当新版选出来。
    """
    return ([int(x) for x in re.findall(r'\d+', name)], name)


def _latest_by_mid(base, filename, mids=MID_LEVELS):
    """在 base 下找含 filename 的**最新版本**目录，跨中间层一起比版本号。

    注意不能「浅层命中就返回」：新旧布局并存时会选中旧版。
    更新方式：把每个中间层下的子目录都收成候选，再按版本号倒序取第一。
    """
    cands = []
    for mid in mids:
        root = os.path.join(base, mid) if mid else base
        if not os.path.isdir(root):
            continue
        try:
            subs = os.listdir(root)
        except OSError:
            continue
        for s in subs:
            p = os.path.join(root, s)
            if os.path.isfile(os.path.join(p, filename)):
                cands.append((_verkey(s), p))
    if not cands:
        return ''
    return max(cands, key=lambda t: t[0])[1]


def find_file_in_install(filename, hint=''):
    """定位含指定文件的剪映目录；找不到返回空串。

    给 draft_builder 找 ffmpeg.exe 用（与 videoeditor.dll 同层级）。
    """
    if hint and os.path.isfile(os.path.join(hint, filename)):
        return hint
    for base in BASES:
        got = _latest_by_mid(base, filename)
        if got:
            return got
    return ''


def find_install_dir(hint=''):
    """定位剪映安装目录（即含 videoeditor.dll 的版本目录）；找不到返回空串。"""
    return find_file_in_install(VIDEOEDITOR, hint)


# ---------------------------------------------------------------------------
# 中文字体探测（P05 §7）
# ---------------------------------------------------------------------------
# 为什么不能写死字体路径
# ----------------------
# P04 及以前，字幕/免责花字的字体是常量：**盘符 + 剪映版本号 + 固定的
# Resources/Font/SystemFont/zh-hans.ttf 路径**，三样都写死。同事的剪映装在
# 别的盘、或升了个版本，这个路径就不存在 —— 草稿里带着一个不存在的 font_path，
# 剪映只能回退默认字体，每条字幕的字号/字形都可能和预演不一致，而**不会报任何错**。
#
# 正确做法：先问"剪映装在哪"（本模块已有的能力），再去它的资源目录里找字体；
# 找不到再退到 Windows 自带中文字体；都没有就返回空串（剪映能安全回退默认）。

# 剪映安装目录下字体所在的相对目录（从具体到宽泛）
FONT_REL_DIRS = ('Resources/Font/SystemFont', 'Resources/Font')

# 优先按名字取：zh-hans.ttf 是剪映自带简中字体，其余为常见系统中文字体
PREFERRED_FONTS = ('zh-hans.ttf', 'zh-Hans.ttf',
                   'msyh.ttc', 'msyh.ttf', 'msyhl.ttc',
                   'SourceHanSansCN-Regular.otf', 'SourceHanSansSC-Regular.otf',
                   'simhei.ttf', 'simsun.ttc', 'Deng.ttf', 'DengXian.ttf',
                   '方正兰亭黑.ttf', '新青年体.ttf')

# Windows 自带中文字体（%SystemRoot%\Fonts 下）
SYSTEM_FONT_NAMES = ('msyh.ttc', 'msyh.ttf', 'msyhbd.ttc', 'simhei.ttf',
                     'simsun.ttc', 'Deng.ttf', 'DengXian.ttf', 'simkai.ttf')

FONT_EXTS = ('.ttf', '.ttc', '.otf')
_CJK_HINT = ('hans', 'han', 'hei', 'song', 'ming', 'kai', 'yuan', 'yahei',
             'deng', 'zh', 'cn', 'sc', 'chinese', '中', '黑', '宋', '楷', '雅黑')


def _first_font_in(d):
    """在目录里挑一个字体文件：优先名字带中文暗示的，其次任意字体。"""
    if not d or not os.path.isdir(d):
        return ''
    try:
        names = [fn for fn in sorted(os.listdir(d))
                 if os.path.splitext(fn)[1].lower() in FONT_EXTS
                 and os.path.isfile(os.path.join(d, fn))]
    except OSError:
        return ''
    if not names:
        return ''
    for fn in names:
        low = fn.lower()
        if any(h in low for h in _CJK_HINT):
            return os.path.join(d, fn)
    return os.path.join(d, names[0])


def find_font(install_dir='', hint=''):
    """定位一个**真实存在**的可用中文字体，返回绝对路径；都没有返回空串。

    优先级：显式 hint > 剪映安装目录内的字体 > Windows 中文字体。
    绝不返回不存在的路径 —— 「找不到就 return ''」，由调用方记录警告，
    而不是把一个假路径写进草稿。
    """
    if hint and os.path.isfile(hint):
        return os.path.abspath(hint)

    base = install_dir or find_install_dir()
    if base:
        for name in PREFERRED_FONTS:
            for sub in FONT_REL_DIRS:
                p = os.path.join(base, sub.replace('/', os.sep), name)
                if os.path.isfile(p):
                    return os.path.abspath(p)
        for sub in FONT_REL_DIRS:
            got = _first_font_in(os.path.join(base, sub.replace('/', os.sep)))
            if got:
                return os.path.abspath(got)

    win = os.path.join(os.environ.get('SystemRoot') or r'C:\Windows', 'Fonts')
    for name in SYSTEM_FONT_NAMES:
        p = os.path.join(win, name)
        if os.path.isfile(p):
            return os.path.abspath(p)
    return ''
