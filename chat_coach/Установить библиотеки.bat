@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   Установка библиотек
echo ============================================
echo.
echo Нужны только для разбора сообщений через Claude.
echo Цифры считаются и без них.
echo.

python --version >nul 2>&1
if errorlevel 1 goto nopython

python -m pip install -r requirements.txt
echo.
echo Готово.
pause
exit /b 0

:nopython
echo [!] Python не найден. Скачай с https://www.python.org/downloads/
echo     и отметь при установке "Add Python to PATH".
pause
exit /b 1
