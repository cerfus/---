@echo off
setlocal
cd /d "%~dp0"

echo Список твоих групп и их номеров.
echo Если группа "Видео дефектов" не находится по названию,
echo возьми отсюда её номер и запусти так:
echo     python download_defects.py --chat -1001234567890
echo.

python download_defects.py --list-chats

pause
