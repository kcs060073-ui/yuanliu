# -*- coding: utf-8 -*-
"""平台抽象层回归（Windows 行为不变 + macOS 分支合理）。

目的：源流 skill 做 macOS 移植时新增了 scripts/platform_paths.py，把原先散落各处的
Windows 硬编码（D:\\JianyingPro / %LOCALAPPDATA% / USERPROFILE\\Desktop / taskkill）
收敛到一份、并补上 macOS 对应物。本测试锁两件事：

  1) **Windows 分支返回值与历史硬编码逐字一致** —— 用本机（Windows）实跑断言，
     确保移植没有"顺手优化"掉 Windows 行为；
  2) **macOS 分支返回结构合理** —— monkeypatch sys.platform='darwin' 验证
     应用根 / 库名 / ffmpeg 名 / 桌面目录 / 外壳重启命令 / 快捷方式规格正确。

monkeypatch 说明：platform_paths 内部用 ``platform_paths.sys.platform`` 判平台，
故直接改 ``platform_paths.sys.platform`` 即可切换分支；与本机真实 OS 无关。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import platform_paths as pp


def _assert_eq(label, got, exp):
    ok = got == exp
    print(('  ✅' if ok else '  ❌'), label, '=', repr(got))
    if not ok:
        print('       期望', repr(exp))
        raise AssertionError('%s: %r != %r' % (label, got, exp))


def test_windows_branch():
    print('[Windows 分支] sys.platform=win32 （本机真实值）')
    assert pp.is_windows() and not pp.is_macos()
    _assert_eq('platform_label', pp.platform_label(), 'windows')

    # 应用安装根：与 jianying_path.BASES 历史值逐字一致
    _assert_eq('jianying_app_roots', pp.jianying_app_roots(), (
        r'D:\JianyingPro',
        r'C:\Program Files\JianyingPro',
        os.path.expandvars(r'%LOCALAPPDATA%\JianyingPro'),
    ))
    # 用户数据根：与 bgm_pool 历史写法逐字一致
    _assert_eq('jianying_user_data_roots', pp.jianying_user_data_roots(), (
        os.path.expandvars(r'%LOCALAPPDATA%\JianyingPro'),
    ))
    _assert_eq('native_lib_name', pp.native_lib_name(), 'videoeditor.dll')
    _assert_eq('ffmpeg_name', pp.ffmpeg_name(), 'ffmpeg.exe')
    # 桌面目录：与 pack_release / install 历史 %USERPROFILE%\Desktop 一致
    _assert_eq('desktop_dir', pp.desktop_dir(),
               os.path.join(os.environ.get('USERPROFILE', ''), 'Desktop'))
    # 外壳重启命令：taskkill explorer
    _assert_eq('shell_restart_cmd', pp.shell_restart_cmd(),
               ['taskkill', '/F', '/IM', 'explorer.exe'])
    # 快捷方式：.bat + 无需 chmod
    _assert_eq('shortcut_spec', pp.shortcut_spec(), ('.bat', False))


def test_macos_branch():
    print('[macOS 分支] monkeypatch sys.platform=darwin')
    saved = pp.sys.platform
    pp.sys.platform = 'darwin'
    try:
        assert pp.is_macos() and not pp.is_windows()
        _assert_eq('platform_label', pp.platform_label(), 'macos')

        roots = pp.jianying_app_roots()
        assert roots and roots[0].endswith('/Applications/VideoFusion-macOS.app/Contents'), \
            '应用根应指向 /Applications/VideoFusion-macOS.app/Contents，实际 %r' % (roots,)
        _assert_eq('native_lib_name', pp.native_lib_name(), 'libvideoeditor.dylib')
        _assert_eq('ffmpeg_name', pp.ffmpeg_name(), 'ffmpeg')

        desk = pp.desktop_dir()
        assert desk.endswith('Desktop') and 'USERPROFILE' not in desk, \
            '桌面目录应是 ~/Desktop，实际 %r' % (desk,)

        # macOS 没有 explorer，外壳重启命令必须返回 None（调用方据此跳过）
        _assert_eq('shell_restart_cmd', pp.shell_restart_cmd(), None)
        # 快捷方式用 .command 且需要 chmod +x
        _assert_eq('shortcut_spec', pp.shortcut_spec(), ('.command', True))

        # 用户数据根落在 ~/Movies/JianyingPro（与 Windows %LOCALAPPDATA%\JianyingPro 同构）
        ud = pp.jianying_user_data_roots()
        assert ud and ud[0].endswith(os.path.join('Movies', 'JianyingPro')), \
            '用户数据根应落在 ~/Movies/JianyingPro，实际 %r' % (ud,)
    finally:
        pp.sys.platform = saved


def test_unknown_platform():
    print('[未知平台分支] monkeypatch sys.platform=linux')
    saved = pp.sys.platform
    pp.sys.platform = 'linux'
    try:
        _assert_eq('platform_label', pp.platform_label(), 'linux')
        # 未知平台：外壳重启 None、快捷方式 .sh + chmod
        _assert_eq('shell_restart_cmd', pp.shell_restart_cmd(), None)
        _assert_eq('shortcut_spec', pp.shortcut_spec(), ('.sh', True))
        _assert_eq('native_lib_name', pp.native_lib_name(), 'videoeditor.dll')
    finally:
        pp.sys.platform = saved


if __name__ == '__main__':
    test_windows_branch()
    test_macos_branch()
    test_unknown_platform()
    print('\n全部通过 ✅')
