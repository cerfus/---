@echo off
setlocal
cd /d "%~dp0"
echo Самопроверка скрипта:
echo.
python test_ru_numbers.py
python test_defects.py
python test_spread.py
python test_dupes.py
echo.
pause
