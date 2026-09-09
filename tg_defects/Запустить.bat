@echo off
chcp 65001 >nul
rem Впиши сюда свои api_id и api_hash с https://my.telegram.org
set TG_API_ID=
set TG_API_HASH=

if "%TG_API_ID%"=="" (
  echo Сначала впиши TG_API_ID и TG_API_HASH в этот файл.
  pause
  exit /b 1
)

python download_defects.py --chat "Видео дефектов" --out "D:\Стекла"
pause
