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

## Phase 1: база и пересборка

Требуется PostgreSQL 16 (SQLite не поддерживается).

```bash
bash scripts/bootstrap_db.sh        # БД, три роли, .env с правами 600
python3 db/migrate.py               # миграции от имени владельца схемы
python3 normalize/normalize.py      # raw -> JSONL (источник истины)
python3 db/load.py                  # JSONL -> PostgreSQL
bash scripts/verify_all.sh          # пересборка с нуля + все тесты
```

**PostgreSQL источником истины не является.** База пересобирается из
`data/*.jsonl` командой `scripts/rebuild_from_jsonl.sh`, и слепок состояния
после пересборки обязан совпасть побайтово.

Роли: `tiktok_owner` владеет схемой, `tiktok_rw` работает в рантайме без
UPDATE и DELETE на append-only таблицах, `tiktok_ro` только читает.
Пароли генерируются при бутстрапе и живут в `.env`, который в git не попадает.

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

## Mobile Control Layer

Телефон как пульт: Telegram показывает состояние системы и не управляет ею.
Слой **только читает** — подключение идёт под ролью `tiktok_ro`, которой
PostgreSQL не выдал ни INSERT, ни UPDATE, ни DELETE. Запрет на изменения
держится базой, а не дисциплиной обработчиков.

Внешних зависимостей нет: долгий опрос Bot API сделан на стандартной
библиотеке. Отдельного источника данных TikTok слой не добавляет.

### Setup

1. Создать бота у @BotFather и получить токен.
2. Узнать свой numeric Telegram user id (например, у @userinfobot).
3. Задать переменные окружения — в `.env` или в окружении процесса.
   В git они не попадают:

```
TELEGRAM_BOT_TOKEN=
TELEGRAM_OWNER_ID=
```

4. Проверить настройку без запуска опроса:

```
python3 -m mobile.telegram --check
```

5. Запустить:

```
python3 -m mobile.telegram
```

Нет токена или нет owner id — интеграция выключается целиком и сообщает
причину. Остальная система при этом работает как раньше.

### Commands

```
/start        — запуск и список команд
/status       — состояние системы
/report       — последний отчёт
/insights     — текущие выводы
/ideas        — идеи контента
/scripts      — сценарии
/experiments  — эксперименты
/queue        — очередь публикации
/refresh      — проверка сервисов (только чтение)
/help         — команды
```

### Безопасность

* белый список из одного владельца; любой другой пользователь получает
  `Access denied.` без внутренних подробностей;
* каждое обращение, включая отказ, пишется в append-only журнал
  `data/mobile/audit.jsonl` с `correlation_id`; журнал в git не попадает;
* токен не хранится в базе, не пишется в журнал и не печатается в логах —
  значения вычищаются редактором перед записью;
* ошибка отдаёт владельцу только `correlation_id`, подробность уходит в
  журнал и лог приложения;
* публикация остаётся выключенной: команд `/publish`, `/submit`, `/send`
  нет ни явных, ни скрытых, а бот не стартует, если
  `system_capabilities.publishing.submit` включён.
