# TikTok AI Manager — @gulyashik52

Система принятия решений по контенту на основании данных аккаунта,
а не общих представлений об алгоритмах TikTok.

**Версия: V0.1 (Фаза 0).** Каркас, протокол и первая выгрузка.
Content DNA ещё не сформирован — не хватает данных, см. `content/dna.md`.

## Как забрать проект на свой компьютер (Windows)

Проект собран в облачном контейнере и живёт в репозитории на GitHub.
Локальная папка `C:\Users\dopok\Desktop\tiktok-ai-manager` наполняется
через клонирование — прямого доступа к диску у ассистента нет.

Сейчас проект лежит подпапкой в репозитории `cerfus/---`, поэтому нужен
промежуточный клон (PowerShell):

```powershell
cd C:\Users\dopok\Desktop
git clone -b claude/tiktok-manager-env-audit-8mh3mg https://github.com/cerfus/--- temp-clone
Move-Item temp-clone\tiktok-ai-manager .\tiktok-ai-manager
Remove-Item -Recurse -Force temp-clone
```

Минус этого варианта: папка на Desktop окажется без истории git —
обновления придётся забирать тем же способом заново.

Если проект вынести в отдельный репозиторий `cerfus/tiktok-ai-manager`,
команда становится одной, а папка остаётся полноценным git-репозиторием:

```powershell
cd C:\Users\dopok\Desktop
git clone https://github.com/cerfus/tiktok-ai-manager
```

Дальше обновления забираются через `git pull`.

Требуется установленный Git для Windows и Python 3.11+.
Проверить: `git --version` и `python --version`.

## Запуск

```bash
python3 analysis/normalize.py   # сверка источников, сборка JSONL из data/raw/
python3 analysis/describe.py    # пересчёт статистики
```

Внешних зависимостей нет — только стандартная библиотека Python.

## Структура

```
CLAUDE.md                 протокол: FACT / HYPOTHESIS / RECOMMENDATION,
                          пороги доказательности, запреты
data/raw/                 сырые выгрузки, не редактируются
data/videos.jsonl         неизменяемые свойства роликов (16)
data/snapshots.jsonl      метрики во времени, по одной строке на источник (32)
analysis/normalize.py     сборка JSONL + сверка двух источников
analysis/describe.py      описательная статистика
content/dna.md            интерпретация, с маркировкой и ссылками на цифры
experiments/register.jsonl реестр гипотез: заводятся ДО публикации
reports/                  отчёты по состоянию на дату
```

## Источники данных

| Роль | Сервис | Статус |
|---|---|---|
| Аналитическое ядро (reach, completion rate, время просмотра) | Supermetrics, `TIKBA` | подключён |
| Комментарии, планирование, публикация | Metricool, бренд `7004662` | подключён |
| Источники трафика (For You / Hashtag / Sound / Search) | — | **недоступны** |
| Прямой TikTok API | — | не подключён |

Оба источника независимо подтверждают views/likes/shares по всем 16
роликам — расхождений нет. Сверка выполняется при каждом `normalize.py`.

## Что дальше

- **Фаза 1** — регулярная выгрузка и накопление снимков во времени.
- **Фаза 2** — Content DNA. Нужно ещё 9+ роликов до порога N=25.
- **Фаза 3** — публикация через очередь ревью Metricool, только с
  подтверждением владельца аккаунта.
