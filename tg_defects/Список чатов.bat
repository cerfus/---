@echo off
chcp 65001 >nul
rem Если скрипт пишет, что группа "Видео дефектов" не найдена - запусти это,
rem найди свою группу в списке и укажи её id в Запустить.bat через --chat
set TG_API_ID=
set TG_API_HASH=

if "%TG_API_ID%"=="" (
  echo Сначала впиши TG_API_ID и TG_API_HASH в этот файл.
  pause
  exit /b 1
)

python download_defects.py --list-chats
pause
