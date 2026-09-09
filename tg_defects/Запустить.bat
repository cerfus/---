@echo off
setlocal
cd /d "%~dp0"

rem ==== Впиши сюда свои api_id и api_hash с https://my.telegram.org ====
set TG_API_ID=
set TG_API_HASH=
rem ====================================================================

if "%TG_API_ID%"=="" goto nokeys

echo Скачиваю ВСЕ видео группы в D:\Стекла
echo Это надолго. Прерывать можно - при повторном запуске продолжит с того же места.
echo.
python download_defects.py
pause
exit /b 0

:nokeys
echo Сначала впиши TG_API_ID и TG_API_HASH в этот файл.
echo Правой кнопкой по файлу - Изменить.
pause
exit /b 1
