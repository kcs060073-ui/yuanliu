# -*- coding: utf-8 -*-
"""YuanLiu workbench desktop installer (invoked by install.bat).

Encoding strategy: source stays pure ASCII (Chinese via \\uXXXX). The PowerShell
body is assembled at runtime and delivered via `powershell -EncodedCommand` as
base64(UTF-16LE), so Chinese paths survive every hop.

What it does:
  1. Builds a REAL multi-size .ico from workbench/yuanliu.jpg:
       Icon.FromHandle(GetHicon()) produces a broken ICO directory (data-length
       larger than the file) that Windows cannot render -> white/default icon.
       Instead we rasterize 256/128/64/48/32/16 PNGs and pack them into a
       proper .ico container (PNG-in-ICO is supported on Vista+).
  2. Creates the desktop shortcut pointing DIRECTLY at
       pythonw.exe  <workbench>/workbench_gui.py
     (no cmd + Chinese .bat hop -> much more reliable to open). Falls back to
     the launcher .bat when no tkinter-capable Python is found.
  3. Unblock-File to strip the Zone.Identifier MOTW, else Windows Security
     refuses the .lnk ("cannot open these files").

Idempotent: existing .ico / .lnk are kept; delete them (or --force) to rebuild.
"""
import argparse
import base64
import glob
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# 桌面目录走平台抽象：Windows 仍是 %USERPROFILE%\Desktop，macOS 是 ~/Desktop。
# 安装器需能独立运行（同事双击就装），故把 scripts 临时挂进 sys.path 再取。
import sys as _sys
_SCRIPTS = os.path.join(os.path.dirname(HERE), 'scripts')
if _SCRIPTS not in _sys.path:
    _sys.path.insert(0, _SCRIPTS)
try:
    import platform_paths as _pp
    DESK = _pp.desktop_dir()
except Exception:
    DESK = os.path.join(os.environ.get('USERPROFILE', ''), 'Desktop')

JPG = os.path.join(HERE, '\u6e90\u6d41.jpg')
ICO = os.path.join(HERE, '\u6e90\u6d41.ico')
BAT = os.path.join(HERE, '\u542f\u52a8\u5de5\u4f5c\u53f0.bat')
GUI = os.path.join(HERE, 'workbench_gui.py')
LNK = os.path.join(DESK, '\u6e90\u6d41\u5de5\u4f5c\u53f0.lnk')
TITLE = '\u6e90\u6d41 \u00b7 \u56ed\u533a\u4fe1\u606f\u6d41\u77ed\u89c6\u9891\u8349\u7a3f\u751f\u6210'

ICO_SIZES = (256, 128, 64, 48, 32, 16)


def _search_dirs():
    """Yield directories to look for an interpreter in, in order.

    PATH first, then the standard python.org install roots -- the same
    fallback the launcher .bat uses. The fallback is not redundant: a freshly
    installed Python updates PATH in the registry only, while the process
    running this installer (and the Explorer that later launches the shortcut)
    still hold the old environment. A PATH-only search then silently degrades
    the shortcut to the .bat launcher.
    """
    for base in os.environ.get('PATH', '').split(os.pathsep):
        if base:
            yield base
    roots = (
        # per-user python.org install: %LOCALAPPDATA%\Programs\Python\Python3xx
        os.path.expandvars(r'%LOCALAPPDATA%\Programs\Python'),
        # all-users python.org install: C:\Program Files\Python3xx (directly
        # under Program Files, NOT under a \Python subdir)
        os.path.expandvars(r'%ProgramFiles%'),
        # alternate layout: C:\Program Files\Python\Python3xx
        os.path.expandvars(r'%ProgramFiles%\Python'),
        os.path.expandvars(r'%ProgramFiles(x86)%'),
        # legacy C:\Python3xx
        'C:\\',
    )
    for root in roots:
        if not os.path.isdir(root):
            continue
        try:
            subs = sorted(glob.glob(os.path.join(root, 'Python3*')), reverse=True)
        except OSError:
            continue
        for s in subs:
            if os.path.isdir(s):
                yield s


def find_pythonw():
    """Return a pythonw/python that can import tkinter (GUI needs it).

    Same strategy as the launcher .bat: take the FIRST candidate that passes
    `import tkinter` (not merely the first python on PATH, which may be a
    tkinter-less embedded runtime). pythonw.exe is tried before python.exe so
    the GUI opens without a console window.
    """
    seen = set()
    dirs = list(_search_dirs())
    for name in ('pythonw.exe', 'python.exe', 'pythonw', 'python'):
        for base in dirs:
            exe = os.path.join(base, name)
            if not os.path.isfile(exe):
                continue
            key = os.path.normpath(exe).lower()
            if key in seen:
                continue
            seen.add(key)
            try:
                r = subprocess.run([exe, '-c', 'import tkinter'],
                                   capture_output=True, timeout=15)
                if r.returncode == 0:
                    return exe
            except Exception:
                continue
    return None


PS_TEMPLATE = r'''
$ErrorActionPreference = 'Stop'
function Log([string]$m) { Write-Host '[YuanLiu] ' $m }
$Jpg='@@JPG@@'; $Ico='@@ICO@@'; $GuiPy='@@GUIPY@@'; $Gui='@@GUI@@'; $Bat='@@BAT@@'; $Lnk='@@LNK@@'
$Sizes = @(@(256,0),@(128,128),@(64,64),@(48,48),@(32,32),@(16,16))

if (-not (Test-Path $Jpg)) { throw 'jpg not found: ' + $Jpg }
if (-not (Test-Path $Gui)) { throw 'gui not found: ' + $Gui }

# ---------- 1. build multi-size PNG-in-ICO ----------
if (Test-Path $Ico) {
    Log 'icon exists'
} else {
    Log 'generating real multi-size icon...'
    Add-Type -AssemblyName System.Drawing
    $src = [System.Drawing.Image]::FromFile($Jpg)
    $pngs = New-Object System.Collections.ArrayList
    foreach ($sz in $Sizes) {
        $n = $sz[0]
        $bmp = New-Object System.Drawing.Bitmap($n, $n)
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
        $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
        $g.Clear([System.Drawing.Color]::Transparent)
        # cover-fit the source into the square canvas
        $sw = $src.Width; $sh = $src.Height
        $scale = [Math]::Min($n / $sw, $n / $sh)
        $dw = [int]($sw * $scale); $dh = [int]($sh * $scale)
        $dx = [int](($n - $dw) / 2); $dy = [int](($n - $dh) / 2)
        $g.DrawImage($src, $dx, $dy, $dw, $dh)
        $g.Dispose()
        $ms = New-Object System.IO.MemoryStream
        $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
        [void]$pngs.Add($ms.ToArray())
        $bmp.Dispose()
    }
    $src.Dispose()
    # pack ICO container
    $fs = [System.IO.File]::Create($Ico)
    $bw = New-Object System.IO.BinaryWriter($fs)
    $bw.Write([uint16]0); $bw.Write([uint16]1); $bw.Write([uint16]$pngs.Count)
    $off = 6 + 16 * $pngs.Count
    for ($i = 0; $i -lt $pngs.Count; $i++) {
        $w = if ($Sizes[$i][0] -ge 256) { 0 } else { $Sizes[$i][0] }
        $h = if ($Sizes[$i][0] -ge 256) { 0 } else { $Sizes[$i][0] }
        $bw.Write([byte]$w); $bw.Write([byte]$h)
        $bw.Write([byte]0); $bw.Write([byte]0)          # palette/reserved
        $bw.Write([uint16]1); $bw.Write([uint16]32)      # planes / bpp
        $bw.Write([uint32]$pngs[$i].Length); $bw.Write([uint32]$off)
        $off += $pngs[$i].Length
    }
    foreach ($p in $pngs) { $bw.Write($p) }
    $bw.Close(); $fs.Close()
    Log ('icon created: ' + $Ico)
}

# ---------- 2. desktop shortcut: pythonw + gui.py (no cmd hop) ----------
if (Test-Path $Lnk) { Log 'desktop shortcut exists' }
else {
    Log 'creating desktop shortcut (pythonw + gui.py)...'
    $w = New-Object -ComObject WScript.Shell
    $l = $w.CreateShortcut($Lnk)
    if ($GuiPy -and (Test-Path $GuiPy)) {
        $l.TargetPath = $GuiPy
        $l.Arguments = '"' + $Gui + '"'
        $l.WorkingDirectory = (Split-Path $Gui -Parent)
    } else {
        $l.TargetPath = $Bat
        $l.WorkingDirectory = (Split-Path $Bat -Parent)
    }
    $l.IconLocation = ($Ico + ',0')
    $l.Description = '@@TITLE@@'
    $l.Save()
    Log ('shortcut created: ' + $Lnk)
    if (-not $GuiPy) { Log '  (no tkinter python found -> shortcut targets the .bat launcher)' }
}

# ---------- 3. strip MOTW ----------
foreach ($p in @($Lnk, $Ico)) {
    if ($p -and (Test-Path $p)) {
        try { Unblock-File -Path $p -ErrorAction SilentlyContinue } catch {}
    }
}
Log 'unblock done'
'''


def build_command(force):
    pyw = find_pythonw()
    body = (PS_TEMPLATE
            .replace('@@JPG@@', JPG)
            .replace('@@ICO@@', ICO)
            .replace('@@GUIPY@@', pyw or '')
            .replace('@@GUI@@', GUI)
            .replace('@@BAT@@', BAT)
            .replace('@@LNK@@', LNK)
            .replace('@@TITLE@@', TITLE))
    if force:
        head = ('Remove-Item -Path $Ico -Force -ErrorAction SilentlyContinue\n'
                'Remove-Item -Path $Lnk -Force -ErrorAction SilentlyContinue\n')
        body = head + body
    enc = base64.b64encode(body.encode('utf-16-le')).decode('ascii')
    return ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-EncodedCommand', enc], pyw


def _shell_restart_cmd():
    """重启外壳的命令（Windows 杀 explorer 以重建图标缓存）。

    平台判定统一走 ``scripts/platform_paths.py`` —— 但安装器本应能独立运行
    （同事双击就装，不该依赖 skill 目录结构），所以这里按需把 scripts 挂进
    sys.path；取不到就按 os.name 兜底，macOS 一律返回 None（没有等价物）。
    """
    try:
        _scripts = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts')
        if _scripts not in sys.path:
            sys.path.insert(0, _scripts)
        import platform_paths
        return platform_paths.shell_restart_cmd()
    except Exception:
        return ['taskkill', '/F', '/IM', 'explorer.exe'] if os.name == 'nt' else None


def refresh_explorer_if_needed(force):
    """force 重建 .ico 后：让 explorer 重建 IconCache.db，否则同名 .ico 仍显示旧图。

    代价：桌面闪一下（explorer 重启 ~2 秒），只在 Windows + --force 时执行。
    macOS 没有 explorer，直接跳过（_shell_restart_cmd() 返回 None）。
    """
    if not force:
        return
    cmd = _shell_restart_cmd()
    if not cmd:
        return
    try:
        print('[YuanLiu] 刷新图标缓存...')
        subprocess.run(cmd, capture_output=True)
        subprocess.Popen(['explorer.exe'])
    except Exception as e:
        print('[YuanLiu] explorer 刷新跳过：', e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true')
    args = ap.parse_args()

    # 本安装器是 Windows 专属（PowerShell 建 .ico/.lnk、重启 explorer）。
    # macOS / Linux 没有等价物，直接告知并退出，避免执行坏掉的 PowerShell。
    if not _pp.is_windows():
        print('[YuanLiu] 当前平台不是 Windows，桌面安装器（.lnk/.ico）暂未实现。')
        print('           macOS 请参考 SKILL.md「跨平台支持状态」小节手动放置脚本 / 用 .command 启动。')
        return 0

    for p in (JPG, GUI):
        if not os.path.isfile(p):
            print('[ERROR] missing required file:', p)
            return 1
    os.makedirs(DESK, exist_ok=True)
    cmd, pyw = build_command(args.force)
    print('[YuanLiu] GUI launcher python:', pyw or '(none -> fallback .bat)')
    rc = subprocess.run(cmd).returncode
    refresh_explorer_if_needed(args.force)
    return rc


if __name__ == '__main__':
    sys.exit(main())
