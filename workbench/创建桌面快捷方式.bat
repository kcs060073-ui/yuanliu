@echo off
chcp 65001 >nul
title YuanLiu Workbench - Create Desktop Shortcut

rem KEEP THIS FILE PURE ASCII -- see the note in the launcher .bat.
rem Chinese guidance lives in shortcut-help.txt (UTF-8), shown via `type`.

set "SKILLS=%USERPROFILE%\.workbuddy\skills"
set "HELP=%~dp0shortcut-help.txt"

if not exist "%SKILLS%" goto :notinstalled

set "FOUND="
for /d %%d in ("%SKILLS%\*") do (
    if exist "%%d\workbench\install.bat" set "FOUND=%%d\workbench\install.bat"
)

if not defined FOUND goto :notinstalled

echo.
echo  Creating the desktop shortcut...
echo.
call "%FOUND%"
echo.
echo  Done - a "YuanLiu Workbench" icon is now on your desktop.
if exist "%HELP%" type "%HELP%"
echo.
pause
exit /b 0

:notinstalled
echo.
echo  [Workbench not found]
if exist "%HELP%" type "%HELP%"
echo.
pause
exit /b 1
