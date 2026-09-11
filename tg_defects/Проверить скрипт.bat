@echo off
setlocal
cd /d "%~dp0"
echo Самопроверка скрипта:
echo.
python test_ru_numbers.py
python test_defects.py
python test_spread.py
python test_dupes.py
python test_text_export.py
python test_by_list.py
python test_plates.py
echo.
pause
