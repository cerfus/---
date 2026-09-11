@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   Копия входа в аккаунт
echo ============================================
echo.
echo Скопирую файлы входа (config.ini и файл сеанса)
echo в указанную папку - для бэкапа или переноса
echo на другой компьютер.
echo.
echo Эта папка станет равносильна паролю от аккаунта -
echo никому не пересылай.
echo.

set /p DEST=Куда сохранить (например D:\backup): 
if "%DEST%"=="" goto empty
python backup_login.py --to "%DEST%"
goto end

:empty
echo Папка не указана - ничего не делаю.

:end
echo.
pause
