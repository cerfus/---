@echo off
REM Применение миграций на Windows. Ни bash, ни psql не требуются —
REM работа идёт через psycopg и DSN из .env.
REM
REM   scripts\migrate.bat           применить недостающие миграции
REM   scripts\migrate.bat --adopt   + признать применёнными те, чьи объекты
REM                                 уже есть в базе (база инициализирована
REM                                 вручную, журнала миграций нет)
setlocal
cd /d "%~dp0.."
echo === Миграции ===
python db\migrate.py %*
if errorlevel 1 (
    echo.
    echo Мигратор вернул ненулевой код. Схема НЕ доведена до конца.
    exit /b 1
)
echo.
echo === Проверка схемы ===
python db\verify_schema.py
exit /b %errorlevel%
