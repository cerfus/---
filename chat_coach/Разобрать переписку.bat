@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   Разбор переписки
echo ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 goto nopython

python -c "import anthropic, pydantic" >nul 2>&1
if errorlevel 1 (
  echo Первый запуск: доставляю библиотеки, это займёт минуту...
  python -m pip install --quiet -r requirements.txt
  echo.
)

echo Открываю окно в браузере...
echo Если оно не появилось - загляни в файл _лог.txt рядом со скриптом.
echo.

rem pythonw запускает без чёрного окна. Если его нет - свёрнутое окно,
rem чтобы запуск не провалился молча.
where pythonw >nul 2>&1
if errorlevel 1 (
  start "Разбор переписки" /min python app.py
) else (
  start "" pythonw app.py
)

timeout /t 3 >nul
exit /b 0

:nopython
echo [!] Python не найден.
echo.
echo     Скачай его с https://www.python.org/downloads/
echo     При установке обязательно отметь галочку "Add Python to PATH".
echo     Потом запусти этот файл снова.
echo.
pause
exit /b 1
