@echo off
REM ---------------------------------------------------------------------
REM  Full project verification on Windows. No bash, no WSL required.
REM
REM    scripts\verify_all.bat             all checks; database NOT recreated
REM    scripts\verify_all.bat --rebuild   + rebuild database from JSONL
REM                                       (needs TIKTOK_DSN_SUPER; DROP DATABASE)
REM
REM  All logic lives in scripts\verify_all.py so that it is identical on
REM  Windows and Linux. This wrapper only locates Python and sets UTF-8.
REM
REM  Two deliberate choices, both to keep cmd.exe from misparsing this file:
REM    * ASCII only. cmd.exe reads a .bat in the console codepage, so
REM      Cyrillic here would break. All Russian output comes from the
REM      Python driver, which sets the console codepage itself.
REM    * No parenthesised IF blocks. A quoted "(0)" inside such a block is
REM      the one construct cmd.exe parses unreliably, so the flow below
REM      uses GOTO instead and stays trivially predictable.
REM ---------------------------------------------------------------------
setlocal
cd /d "%~dp0.."

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

REM Probe by actually running the interpreter, not with "where": the
REM Microsoft Store alias python.exe answers "where" but only opens the
REM Store when executed.
REM "python" is tried first on purpose: inside an activated virtualenv it
REM is the venv interpreter, while the "py" launcher would silently pick
REM the system one and check the wrong site-packages.
set "PY="

python -c "import sys; raise SystemExit(0 if sys.version_info[0]==3 else 1)" >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY goto :found

py -3 -c "import sys; raise SystemExit(0)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if defined PY goto :found

echo [ERROR] Working Python 3 not found in PATH.
echo         Install Python 3, or add it to PATH, then run again.
exit /b 2

:found
%PY% scripts\verify_all.py %*
set "RC=%errorlevel%"
if "%RC%"=="0" goto :done
echo.
echo [FAILED] Verification did not pass. Exit code %RC%.

:done
exit /b %RC%
