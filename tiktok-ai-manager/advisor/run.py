#!/usr/bin/env python3
"""Советник из командной строки.

    python -m advisor.run analyze              что залетело и чем отличается
    python -m advisor.run ideas                идеи (модель, если есть ключ)
    python -m advisor.run ideas --offline      идеи без модели
    python -m advisor.run ideas --out DIR      куда сохранить (по умолчанию data/ideas)

Разбор ничего не пишет. Идеи сохраняются в data/ideas/ в двух видах:
.json — полностью, вместе с отброшенным и причинами; .txt — для чтения.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from advisor import analysis as A, ideas as I     # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="Советник по роликам.")
    ap.add_argument("command", choices=("analyze", "ideas"))
    ap.add_argument("--offline", action="store_true",
                    help="не обращаться к модели даже при заданном ключе")
    ap.add_argument("--out", default=str(I.OUT_DIR),
                    help="каталог для сохранения идей")
    ap.add_argument("--payload",
                    help="готовый ответ (JSON по схеме ideas.SCHEMA) — проверить и сохранить")
    args = ap.parse_args(argv)

    a = A.analyze()
    if args.command == "analyze":
        print(A.render(a))
        return 0
    if args.payload:
        payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
        r = I.from_payload(payload, a)
    else:
        if not args.offline:
            print("Готовлю идеи. С моделью это до пары минут…", flush=True)
        r = I.generate(a, offline=args.offline)
    print(I.render(r))
    j, t = I.save(r, args.out)
    print(f"\nсохранено: {t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
