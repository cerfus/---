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
echo [1/3] Обновляю pip...
python -m pip install --upgrade pip

echo.
echo [2/3] Ставлю распознавание речи, это займёт несколько минут...
python -m pip install faster-whisper
if errorlevel 1 goto pipfail
echo [ок] Распознавание речи готово.

echo.
echo [3/3] Ставлю telethon (нужен только для скачивания напрямую)...
python -m pip install telethon
if errorlevel 1 goto telethonfail
echo [ок] telethon готов.
goto ffmpeg

:telethonfail
echo.
echo [!] telethon не встал. Это не страшно:
echo     путь через выгрузку ("Разложить выгрузку.bat") работает без него.
echo     Не заработает только прямое скачивание ("Проба.bat", "Запустить.bat").

:ffmpeg
echo.
echo Проверяю ffmpeg...
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
echo   Готово. Дальше два пути:
echo.
echo   Через выгрузку из Телеграма (без api_id):
echo      запусти "Разложить выгрузку.bat"
echo.
echo   Прямое скачивание (нужен api_id):
echo      запусти "Проба.bat"
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
