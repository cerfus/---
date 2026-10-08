@echo off
REM ---------------------------------------------------------------------
REM  AI Manager - double-click to open. Same as scripts\menu.bat.
REM  ASCII only and no parenthesised IF blocks, like the other .bat files.
REM ---------------------------------------------------------------------
cd /d "%~dp0"
REM One line: the menu may update this file (git pull); see scripts\menu.bat.
call scripts\menu.bat %* && exit /b 0 || exit /b 1
