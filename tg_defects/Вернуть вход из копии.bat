@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   Вернуть вход из копии
echo ============================================
echo.
echo Возьму config.ini и файл сеанса из указанной
echo папки и положу рядом со скриптом. После этого
echo вход работает без номера и кода.
echo.

set /p SRC=Из какой папки взять (например D:\backup): 
if "%SRC%"=="" goto empty
python backup_login.py --from "%SRC%"
goto end

:empty
echo Папка не указана - ничего не делаю.

:end
echo.
pause
