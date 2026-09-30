#!/usr/bin/env bash
# Инструменты промера видео. Не обязательны: без них Tier 1 честно
# отдаёт unavailable, а не выдумывает значения. Поэтому скрипт НИКОГДА
# не валит сборку — он сообщает, что доступно, и возвращает 0.
cd "$(dirname "$0")/.."
if python3 -c "import cv2, numpy, imageio_ffmpeg" 2>/dev/null; then
  echo "  инструменты промера уже установлены"
else
  echo "  установка инструментов промера из requirements-extract.txt"
  pip install --quiet --disable-pip-version-check -r requirements-extract.txt \
    2>/dev/null || echo "  установить не удалось — Tier 1 останется unavailable"
fi
python3 - <<'PY'
import sys; sys.path.insert(0, '.')
from assets import probe
t = probe.tooling()
for k, v in sorted(t.items()):
    print(f"  {k:<10}{v or 'ОТСУТСТВУЕТ'}")
print(f"  extractor_version: {probe.extractor_version(t)}")
PY
exit 0
