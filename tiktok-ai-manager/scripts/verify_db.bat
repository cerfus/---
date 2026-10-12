@echo off
REM Проверка состояния БД и стартовой конфигурации мобильного слоя.
REM Ничего не изменяет. Telegram не запускается.
setlocal
cd /d "%~dp0.."
echo === Схема базы ===
python db\verify_schema.py
set SCHEMA_RC=%errorlevel%
echo.
echo === Стартовая проверка мобильного слоя ===
python -m mobile.verify
set MOBILE_RC=%errorlevel%
echo.
echo === Итог ===
echo   схема:          %SCHEMA_RC%  ^(0 = в порядке^)
echo   мобильный слой: %MOBILE_RC%  ^(0 = состояние ожидаемое^)
if not "%SCHEMA_RC%"=="0" exit /b 1
if not "%MOBILE_RC%"=="0" exit /b 1
exit /b 0
