@echo off
chcp 65001 >nul
echo ============================================
echo   Установка всего, что нужно скрипту
echo ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
  echo [!] Python не найден.
  echo     Скачай с https://www.python.org/downloads/
  echo     ПРИ УСТАНОВКЕ ОБЯЗАТЕЛЬНО ПОСТАВЬ ГАЛОЧКУ "Add Python to PATH"
  echo     Потом запусти этот файл снова.
  pause
  exit /b 1
)
echo [ок] Python найден.

echo.
echo [1/2] Ставлю библиотеки...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo [!] Не удалось поставить библиотеки. Покажи мне текст ошибки выше.
  pause
  exit /b 1
)

echo.
echo [2/2] Проверяю ffmpeg...
ffmpeg -version >nul 2>&1
if errorlevel 1 (
  echo     ffmpeg не найден, ставлю через winget...
  winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
  echo.
  echo [!] ВАЖНО: закрой это окно и открой заново, чтобы ffmpeg заработал.
) else (
  echo [ок] ffmpeg на месте.
)

echo.
echo Проверка распознавания номеров квартир:
python test_ru_numbers.py

echo.
echo ============================================
echo   Готово. Дальше:
echo   1) открой my.telegram.org, возьми api_id и api_hash
echo   2) впиши их в файл "Запустить.bat"
echo   3) запусти "Проба.bat" - проверка на 10 видео
echo ============================================
pause
