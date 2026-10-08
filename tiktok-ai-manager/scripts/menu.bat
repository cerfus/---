@echo off
REM ---------------------------------------------------------------------
REM  AI Manager console menu. Double-click it or run from CMD.
REM  No bash, no WSL. All logic lives in scripts\menu.py.
REM
REM  Same two rules as verify_all.bat, for the same reason (cmd.exe parses
REM  a .bat in the console codepage and misparses parenthesised blocks):
REM    * ASCII only; Russian text is printed by Python.
REM    * No parenthesised IF blocks; flow uses GOTO.
REM ---------------------------------------------------------------------
setlocal
cd /d "%~dp0.."

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

REM "python" first: inside a virtualenv it is the venv interpreter.
set "PY="

python -c "import sys; raise SystemExit(0 if sys.version_info[0]==3 else 1)" >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY goto :found

py -3 -c "import sys; raise SystemExit(0)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if defined PY goto :found

echo [ERROR] Working Python 3 not found in PATH.
echo         Install Python 3, or add it to PATH, then run again.
REM pause: when started by double-click the window would close at once
pause
exit /b 2

:found
REM The launch and the jump after it MUST stay on one line. cmd.exe reads a
REM .bat line by line from a byte offset; the menu can update this very file
REM (git pull), and the next line would then be read from the middle of the
REM new file. A line is parsed whole before it runs, and GOTO looks the label
REM up in the current file, so this line is safe across updates.
%PY% scripts\menu.py %* && goto :menu_ok || goto :menu_failed

:menu_ok
exit /b 0

:menu_failed
REM On an error keep the window open: a double-clicked window would
REM otherwise close before the message could be read.
pause
exit /b 1
