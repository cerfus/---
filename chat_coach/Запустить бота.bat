@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   Разбор переписки - бот
echo ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 goto nopython

if not exist ".env" goto noenv

python -c "import aiogram, anthropic, pydantic" >nul 2>&1
if errorlevel 1 (
  echo Первый запуск: доставляю библиотеки, это займёт минуту...
  python -m pip install --quiet -r requirements.txt
  echo.
)

echo Запускаю. Остановить - Ctrl+C или просто закрыть это окно.
echo.
python bot.py

echo.
echo Бот остановился. Окно оставлено открытым, чтобы была видна причина.
pause
exit /b 0

:noenv
echo [!] Нет файла .env - в нём лежат ключи.
echo.
echo     1. Скопируй .env.example и назови копию .env
echo     2. Впиши в него BOT_TOKEN, ANTHROPIC_API_KEY и CHAT_COACH_ADMIN
echo     3. Запусти этот файл снова
echo.
echo     Один раз - дальше только двойной клик. Ключи в чат
echo     пересылать не надо никогда.
echo.
pause
exit /b 1

:nopython
echo [!] Python не найден.
echo.
echo     Скачай его с https://www.python.org/downloads/
echo     При установке обязательно отметь галочку "Add Python to PATH".
echo     Потом запусти этот файл снова.
echo.
pause
exit /b 1
