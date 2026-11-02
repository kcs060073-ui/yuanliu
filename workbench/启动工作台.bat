@echo off
setlocal
cd /d "%~dp0"

rem ---------------------------------------------------------------
rem  KEEP THIS FILE PURE ASCII. cmd.exe reads a .bat byte-wise; a
rem  non-ASCII byte desyncs its line buffer (chcp changes the code
rem  page mid-read) and it starts executing pieces of comments.
rem  Chinese text belongs in the Python / GUI layer, not here.
rem ---------------------------------------------------------------

rem Pick the first python/pythonw that can import tkinter. Several runtimes
rem may coexist (WorkBuddy's bundled Python has no tkinter, the official one
rem does), so the first match on PATH is not necessarily a usable one.
set PYW=

for /f "delims=" %%i in ('where python 2^>nul') do (
  if not defined PYW (
    "%%i" -c "import tkinter" >nul 2>nul
    if not errorlevel 1 (
      if exist "%%~dpipythonw.exe" (
        set PYW=%%~dpipythonw.exe
      ) else (
        set PYW=%%i
      )
    )
  )
)
if not defined PYW (
  for /f "delims=" %%i in ('where pythonw 2^>nul') do (
    if not defined PYW (
      "%%i" -c "import tkinter" >nul 2>nul
      if not errorlevel 1 set PYW=%%i
    )
  )
)

rem Fallback: a freshly installed Python updates PATH in the registry only, so
rem this session (and any Explorer already running) still sees the old value.
rem Scan the standard install roots, newest version first.
if not defined PYW call :probe "%LOCALAPPDATA%\Programs\Python"
if not defined PYW call :probe "%ProgramFiles%"
if not defined PYW call :probe "%ProgramFiles%\Python"
if not defined PYW call :probe "%ProgramFiles(x86)%"
if not defined PYW call :probe "C:"

if not defined PYW (
  echo [ERROR] No Python 3 with tkinter was found.
  echo.
  echo   Install the official build from python.org and tick
  echo   "Add python.exe to PATH", or make sure tcl/tk is included.
  echo.
  pause
  exit /b 1
)

start "" "%PYW%" "%~dp0workbench_gui.py"
exit /b 0

rem Scan <root>\Python3* (newest version first); first usable one wins.
:probe
for /f "delims=" %%d in ('dir /b /ad /o-n "%~1\Python3*" 2^>nul') do (
  if not defined PYW (
    if exist "%~1\%%d\pythonw.exe" (
      "%~1\%%d\pythonw.exe" -c "import tkinter" >nul 2>nul
      if not errorlevel 1 set "PYW=%~1\%%d\pythonw.exe"
    )
  )
)
exit /b
