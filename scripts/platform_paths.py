# -*- coding: utf-8 -*-
"""剪映相关路径的**平台抽象**（Windows / macOS） —— 全工程单一实现。

为什么单独成一个模块
--------------------
工程里原先把 Windows 路径直接写死，散在 4 个地方：

    scripts/jianying_path.py   D:\\JianyingPro / C:\\Program Files\\JianyingPro / %LOCALAPPDATA%\\JianyingPro
    scripts/bgm_pool.py        %LOCALAPPDATA%\\JianyingPro\\User Data\\Cache\\music
    scripts/pack_release.py    %USERPROFILE%\\Desktop
    workbench/install.py       taskkill /F /IM explorer.exe（重启资源管理器）

这些在 macOS 上**不是报错，而是静默失效** —— ``os.path.expandvars('%LOCALAPPDATA%')``
在 Mac 上原样返回 ``'%LOCALAPPDATA%'``，于是拼出一条不存在的路径，
表现为"找不到剪映 / 找不到 BGM / 找不到桌面"，而报错信息完全看不出根因。
所以收敛到这里一份，其余模块只做薄封装。

约定（重要）
----------
**win32 分支的返回值必须与历史硬编码逐字一致** —— 平台抽像是为 macOS 新开的口子，
绝不能顺手"优化" Windows 行为；任何改动都要先跑 ``tests/test_platform_paths.py``
与既有全量回归。

macOS 对应位置（2026-10 查证，公开资料 + 社区 skill）
--------------------------------------------------
    应用本体   /Applications/VideoFusion-macOS.app（bundle id com.lemon.lvpro）
    用户数据   ~/Movies/JianyingPro               ← 对应 Windows %LOCALAPPDATA%\\JianyingPro
    音乐缓存   ~/Movies/JianyingPro/User Data/Cache/music
    草稿       ~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft

即：**两边布局是同构的**，都是 ``<用户数据根>/User Data/Cache/music``。
真正不同构的只有"加解密 native 库"这一层（Windows 是 PE 的 videoeditor.dll，
macOS 是 Mach-O），那部分在 ``jy_crypto.py`` 里单独处理，本模块只负责**找得到它**。
"""
import os
import sys

# ---------------------------------------------------------------- 平台判定

def is_windows():
    return sys.platform.startswith('win')


def is_macos():
    return sys.platform == 'darwin'


def platform_label():
    if is_windows():
        return 'windows'
    if is_macos():
        return 'macos'
    return sys.platform or 'unknown'


# -------------------------------------------------- 剪映「用户数据根」

# 相对用户数据根的子路径：两个平台一致（同构）
MUSIC_CACHE_REL = ('User Data', 'Cache', 'music')
DRAFT_PROJECTS_REL = ('User Data', 'Projects', 'com.lveditor.draft')


def jianying_user_data_roots():
    """剪映**用户数据根**（音乐缓存 / 内部项目的父目录），按优先级排。"""
    if is_windows():
        # 与 bgm_pool.py 历史写法逐字一致
        return (os.path.expandvars(r'%LOCALAPPDATA%\JianyingPro'),)
    if is_macos():
        return (
            os.path.join(os.path.expanduser('~'), 'Movies', 'JianyingPro'),
            os.path.join(os.path.expanduser('~'), 'Movies', 'CapCut'),
        )
    return ()


def music_cache_dirs():
    """剪映音乐缓存目录候选（已过滤不存在者由调用方处理）。"""
    return [os.path.join(root, *MUSIC_CACHE_REL)
            for root in jianying_user_data_roots()]


def draft_projects_dirs():
    """剪映内部项目目录候选（com.lveditor.draft 之上）。"""
    return [os.path.join(root, *DRAFT_PROJECTS_REL)
            for root in jianying_user_data_roots()]


# -------------------------------------------------- 剪映「应用安装根」

def jianying_app_roots():
    """剪映**应用安装根**（其下再按 MID 层找版本目录与 native 库）。

    Windows 的三个候选与 jianying_path.BASES 历史值逐字一致。
    macOS 用 app 包的 Contents 目录（bundle id com.lemon.lvpro）。
    """
    if is_windows():
        return (
            r'D:\JianyingPro',
            r'C:\Program Files\JianyingPro',
            os.path.expandvars(r'%LOCALAPPDATA%\JianyingPro'),
        )
    if is_macos():
        return (
            '/Applications/VideoFusion-macOS.app/Contents',
            '/Applications/剪映专业版.app/Contents',
            os.path.join(os.path.expanduser('~'), 'Applications',
                         'VideoFusion-macOS.app', 'Contents'),
        )
    return ()


def app_mid_levels():
    """版本目录之上的**中间层**候选（新版剪映会多套一层）。

    Windows：''（旧布局 <base>\\<版本>）与 'Apps'（新版启动器）。
    macOS：app 包内的常见二进制层（MacOS / Frameworks），外加 '' 兜底。
    """
    if is_windows():
        return ('', 'Apps')
    if is_macos():
        return ('', 'MacOS', 'Frameworks')
    return ('',)


def native_lib_name():
    """剪映加解密 native 库的文件名（不含目录）。"""
    if is_windows():
        return 'videoeditor.dll'
    if is_macos():
        # Mach-O 动态库；具体名字需在真机确认（可能是 libvideoeditor.dylib /
        # libVECreator.dylib / libvecryptor.dylib 之一，见 jy_crypto 的说明）
        return 'libvideoeditor.dylib'
    return 'videoeditor.dll'


def ffmpeg_name():
    """剪映随包 ffmpeg 的可执行文件名。"""
    return 'ffmpeg.exe' if is_windows() else 'ffmpeg'


# -------------------------------------------------- 草稿库（用户看到的草稿）

def draft_library_roots():
    """剪映**草稿库**（界面里那条草稿列表所在目录）候选。

    Windows 默认 D:\\JianyingPro Drafts（也是本工程 CLI 的缺省输出目录）。
    macOS 没有统一的官方命名，给出两个常见候选 + 内部项目目录兜底；
    实际使用时**优先以 CLI --out / 工作台设置为准**，这里只做缺省探测。
    """
    if is_windows():
        return (r'D:\JianyingPro Drafts',)
    if is_macos():
        home = os.path.expanduser('~')
        return (
            os.path.join(home, 'Movies', 'JianyingPro Drafts'),
            os.path.join(home, 'Movies', 'JianyingPro'),
        ) + tuple(draft_projects_dirs())
    return ()


# -------------------------------------------------- 桌面 / 快捷方式

def desktop_dir():
    """桌面目录（打包器取手册、快捷方式落点用）。"""
    if is_windows():
        return os.path.join(os.environ.get('USERPROFILE', ''), 'Desktop')
    return os.path.join(os.path.expanduser('~'), 'Desktop')


def shortcut_spec():
    """桌面快捷方式的 (扩展名, 是否需要 chmod +x)。"""
    if is_windows():
        return ('.bat', False)
    if is_macos():
        # macOS 上 .command 双击即由 Terminal 执行
        return ('.command', True)
    return ('.sh', True)


def shell_restart_cmd():
    """安装器建完快捷方式后，用于刷新外壳的命令（Windows 重启资源管理器）。

    macOS / 其它平台返回 None —— 不需要（也没有等价物），调用方直接跳过。
    """
    if is_windows():
        return ['taskkill', '/F', '/IM', 'explorer.exe']
    return None
