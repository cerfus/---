@echo off
REM ---------------------------------------------------------------------
REM  AI Manager - double-click to open. Same as scripts\menu.bat.
REM  ASCII only and no parenthesised IF blocks, like the other .bat files.
REM ---------------------------------------------------------------------
cd /d "%~dp0"
call scripts\menu.bat %*
