@echo off
rem ============================================================
rem  YuanLiu workbench desktop shortcut installer (idempotent)
rem
rem  Real work is done by install.py (same folder). This .bat only
rem  locates a Python and calls it.
rem
rem  Callers: double-click / workbench GUI first-run / upgrade.py.
rem  No registry, no service. Uninstall = delete the desktop
rem  shortcut and workbench\yuanliu.ico
rem
rem  KEEP THIS FILE PURE ASCII -- see the note in the launcher .bat.
rem ============================================================

setlocal
set "SELF=%~dp0install.py"

rem install.py itself needs no tkinter; only the SHORTCUT TARGET does, and
rem install.py resolves that on its own. So any working Python 3 will do:
rem   1. the Python bundled with WorkBuddy (any version, not hardcoded)
rem   2. an official python.org install (PATH may not be refreshed yet)
rem   3. whatever PATH offers
set "PY="
set "WB=%USERPROFILE%\.workbuddy\binaries\python\versions"
for /f "delims=" %%d in ('dir /b /ad /o-n "%WB%\3*" 2^>nul') do (
    if not defined PY if exist "%WB%\%%d\python.exe" set "PY=%WB%\%%d\python.exe"
)
if not defined PY call :probe "%LOCALAPPDATA%\Programs\Python"
if not defined PY call :probe "%ProgramFiles%\Python"
if not defined PY call :probe "C:"
if not defined PY (
    for /f "delims=" %%i in ('where python 2^>nul') do (
        if not defined PY set "PY=%%i"
    )
)

if not defined PY (
    echo [ERROR] Python not found. Install Python 3 from python.org,
    echo         or reinstall WorkBuddy, which ships its own Python.
    exit /b 1
)

"%PY%" -X utf8 "%SELF%" %*
exit /b %ERRORLEVEL%

rem Scan <root>\Python3* (newest version first); first usable one wins.
:probe
for /f "delims=" %%d in ('dir /b /ad /o-n "%~1\Python3*" 2^>nul') do (
    if not defined PY (
        if exist "%~1\%%d\python.exe" set "PY=%~1\%%d\python.exe"
    )
)
exit /b
