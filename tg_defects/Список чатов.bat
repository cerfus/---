@echo off
setlocal
cd /d "%~dp0"

rem Если скрипт пишет, что группа "Видео дефектов" не найдена - запусти это,
rem найди свою группу в списке и укажи её номер в Запустить.bat вот так:
rem     python download_defects.py --chat -1001234567890

rem ==== Впиши сюда свои api_id и api_hash с https://my.telegram.org ====
set TG_API_ID=
set TG_API_HASH=
rem ====================================================================

if "%TG_API_ID%"=="" goto nokeys

python download_defects.py --list-chats
pause
exit /b 0

:nokeys
echo Сначала впиши TG_API_ID и TG_API_HASH в этот файл.
pause
exit /b 1
