@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   Установка всего, что нужно скрипту
echo ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 goto nopython
echo [ок] Python найден.

echo.
echo [1/2] Ставлю библиотеки, это займёт несколько минут...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 goto pipfail

echo.
echo [2/2] Проверяю ffmpeg...
ffmpeg -version >nul 2>&1
if errorlevel 1 goto noffmpeg
echo [ок] ffmpeg на месте.
goto checkparser

:noffmpeg
echo     ffmpeg не найден, ставлю через winget...
winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
echo.
echo [!] ВАЖНО: закрой это окно и открой заново, иначе ffmpeg не подхватится.

:checkparser
echo.
echo Проверка распознавания номеров квартир:
python test_ru_numbers.py

echo.
echo ============================================
echo   Готово. Дальше просто запусти "Проба.bat"
echo   Скрипт сам спросит api_id и api_hash
echo   и подскажет, где их взять.
echo ============================================
pause
exit /b 0

:nopython
echo [!] Python не найден.
echo     Скачай его с https://www.python.org/downloads/
echo     ПРИ УСТАНОВКЕ ПОСТАВЬ ГАЛОЧКУ "Add Python to PATH"
echo     Потом запусти этот файл снова.
pause
exit /b 1

:pipfail
echo.
echo [!] Не удалось поставить библиотеки. Покажи текст ошибки выше.
pause
exit /b 1
