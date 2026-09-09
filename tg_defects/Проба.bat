@echo off
setlocal
cd /d "%~dp0"

rem ==== Впиши сюда свои api_id и api_hash с https://my.telegram.org ====
set TG_API_ID=
set TG_API_HASH=
rem ====================================================================

if "%TG_API_ID%"=="" goto nokeys

echo Пробный прогон: 10 последних видео из группы.
echo Скачается в D:\Стекла - посмотришь, верно ли определились квартиры.
echo.
python download_defects.py --limit 10
echo.
echo Открой D:\Стекла и файл _отчет.csv - проверь номера квартир.
echo Если всё верно - запускай "Запустить.bat" на весь архив.
pause
exit /b 0

:nokeys
echo Сначала впиши TG_API_ID и TG_API_HASH в этот файл.
echo Правой кнопкой по файлу - Изменить.
echo Взять их тут: https://my.telegram.org - API development tools
pause
exit /b 1
