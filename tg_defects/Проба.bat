@echo off
chcp 65001 >nul
rem Впиши сюда свои api_id и api_hash с https://my.telegram.org
set TG_API_ID=
set TG_API_HASH=

if "%TG_API_ID%"=="" (
  echo Сначала впиши TG_API_ID и TG_API_HASH в этот файл.
  echo Взять их: https://my.telegram.org - API development tools
  pause
  exit /b 1
)

echo Пробный прогон на 10 последних видео.
echo Скачается в D:\Стекла - посмотришь, верно ли определились квартиры.
echo.
python download_defects.py --chat "Видео дефектов" --out "D:\Стекла" --limit 10
echo.
echo Открой D:\Стекла и файл _отчет.csv - проверь номера.
echo Если всё верно - запускай "Запустить.bat" на весь архив.
pause
