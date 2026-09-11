@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   Проверка видео: целые ли и чем открывать
echo ============================================
echo.
echo Покажет, каким кодеком сняты видео и есть ли
echo среди них по-настоящему битые.
echo.
echo Если видео снято в HEVC (H.265) - встроенный
echo "Медиаплеер" Windows его не откроет, и файл
echo тут ни при чём. Нужен VLC: https://videolan.org
echo.

python check_videos.py

echo.
echo Если хочешь проверить надёжнее - каждый файл
echo раскодируется целиком (долго, минут 10):
echo     python check_videos.py --deep
echo.
pause
