# Telegram Post Archive Love

Статус: первый controlled-run выполнен для канала `@andrey_i_vika`, локальная SQLite-база собрана.

## Назначение

Проект проходит по публичным URL вида `https://t.me/<channel>/<post_id>`, извлекает содержимое постов и сохраняет архив в SQLite с возможностью безопасного перезапуска.

В зону ответственности входит:

- обход диапазона `post_id`;
- сохранение текста, даты, media metadata и raw HTML;
- фиксация статусов `success`, `missing`, `error`;
- пропуск уже обработанных постов;
- retry с exponential backoff;
- dry-run без записи в базу.

Не входит:

- скачивание файлов media;
- работа с приватными каналами;
- сложная нормализация контента;
- browser automation.

## Структура проекта

```text
telegram-post-archive_love/
  .gitignore
  README.md
  requirements.txt
  parser.py
  publish_max.py
  yandex_album_scraper.py
  podcast_link_mapper.py
  db.py
  formatter.py
  max_client.py
  models.py
  utils.py
  docs/
    PLAN.md
    STATUS.md
```

## Стек

- Python 3.11+
- httpx
- BeautifulSoup4
- sqlite3
- logging
- playwright

## CLI

```bash
python parser.py \
  --channel andrey_i_vika \
  --start 4 \
  --end 158 \
  --db posts.db \
  --delay-min 0.5 \
  --delay-max 1.5 \
  --retries 3
```

Дополнительно:

- без `--end` парсер идёт до текущего края канала на дату запуска и останавливается после серии подряд отсутствующих постов;
- `--dry-run` — только логирование без записи в SQLite
- `--limit 100` — обработать только `N` новых постов
- `--latest-gap-threshold 20` — порог подряд отсутствующих постов для авто-остановки в режиме без `--end`

Публикация в MAX:

```bash
python publish_max.py \
  --db posts.db \
  --token YOUR_TOKEN \
  --chat CHAT_ID \
  --limit 100 \
  --delay-min 3.0 \
  --delay-max 6.0 \
  --retries 3
```

## Данные в SQLite

Таблица `posts` хранит:

- `telegram_post_id`
- `url`
- `published_at`
- `text_html`
- `text_plain`
- `has_media`
- `media_type`
- `media_urls`
- `raw_html`
- `status`
- `error`
- `created_at`
- `published_to_max`
- `max_message_id`
- `published_at_max`

## Быстрый старт

```bash
cd /home/deploy/app/telegram-post-archive_love
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python parser.py --channel andrey_i_vika --start 4 --end 158 --db posts.db
```

Для безопасной первичной проверки без записи в SQLite:

```bash
python parser.py --channel andrey_i_vika --start 4 --end 158 --db posts.db --dry-run
```

Актуальный зафиксированный диапазон для controlled-run на `2026-03-24`:

- стартовый пост: `https://t.me/andrey_i_vika/4`
- последний известный пост на `2026-03-24`: `https://t.me/andrey_i_vika/158`

## Интерактивный сбор треков Яндекс Музыки

Для задач, где нужно безопасно сопоставить Telegram podcast-ссылки и реальные Yandex Music track URL, в проект добавлен отдельный браузерный сборщик:

```bash
python yandex_album_scraper.py \
  --album-url "https://music.yandex.ru/album/36214929?utm_source=web&utm_medium=copy_link" \
  --proxy-server "socks5://127.0.0.1:17890" \
  --output-json yandex_album_tracks.json \
  --output-csv yandex_album_tracks.csv
```

Что делает скрипт:

- открывает альбом в Chromium через Playwright;
- сохраняет persistent browser profile в `.playwright-yandex-profile/`;
- позволяет вручную пройти login/captcha в окне браузера;
- после загрузки альбома прокручивает страницу и собирает `track_id`, `track_url`, `title`;
- сохраняет результат сразу в JSON и CSV.

Практический нюанс:

- этот сценарий нужен именно для интерактивной работы с капчей и anti-bot;
- в полностью headless-режиме на сервере Yandex Music может отдавать `SmartCaptcha`;
- если нужен удалённый IP, можно подать `--proxy-server socks5://...` через SSH SOCKS proxy.

## Безопасная замена podcast-ссылок в базе

После сбора `yandex_album_tracks.json` можно построить безопасный mapping и, при необходимости, применить его к `posts.db`:

```bash
python podcast_link_mapper.py \
  --db posts.db \
  --tracks-json yandex_album_tracks.json \
  --report-json podcast_link_mapping_report.json
```

Что делает скрипт:

- находит в базе все `https://t.me/mavestreambot/app?startapp=lovebusiness...`;
- извлекает название эпизода из anchor text или из заголовка текущего поста;
- сопоставляет эпизод с Yandex Music не по порядку, а по совпадению названия;
- сохраняет отчёт с `resolved` и `unresolved` кейсами;
- в режиме `--apply` переписывает только подтверждённые ссылки.

Применение с backup:

```bash
python podcast_link_mapper.py \
  --db posts.db \
  --tracks-json yandex_album_tracks.json \
  --report-json podcast_link_mapping_report.json \
  --apply \
  --backup-db
```

## Git и GitHub

Проект находится в `/home/deploy/app/telegram-post-archive_love` как отдельный git-репозиторий.

Текущий репозиторий:

- `https://github.com/Sheshenin/telegram-post-archive_love`
- основная ветка: `main`

Исходный репозиторий-источник:

- `https://github.com/Sheshenin/telegram-post-archive`

Базовый workflow:

1. обновить код и документацию вместе;
2. проверить `git config user.name` и `git config user.email`;
3. сделать commit в папке проекта;
4. push в GitHub.

В репозиторий не должны попадать:

- `.venv/`
- `__pycache__/`
- `posts.db`
- локальные временные артефакты

## Ограничения и допущения

- парсер рассчитан на публичные каналы и Telegram widget markup;
- `media_urls` сохраняются как JSON-строка без скачивания файлов;
- для `missing` и `error` создаются записи в базе, чтобы не терять прогресс;
- если `--end` не задан, проход идёт до актуального края канала на дату запуска и останавливается после `--latest-gap-threshold` подряд записей `missing`;
- нестандартные или редко встречающиеся блоки Telegram не нормализуются отдельно.

## Текущее состояние

- реализован устойчивый SQLite storage с индексами;
- реализован HTTP fetcher с браузерным `User-Agent`, timeout и retry;
- реализован HTML parser под Telegram widget markup;
- fetch переведён на `https://t.me/s/<channel>/<post_id>`, потому что прямой URL поста отдаёт только embed shell;
- реализована очистка `text_html` с сохранением только `b`, `i`, `a`, `br`;
- реализован режим авто-остановки на текущем крае канала без ручного `--end`;
- добавлен `dry-run` и `limit`;
- добавлен publish pipeline в MAX через `publish_max.py`, `formatter.py`, `max_client.py`;
- в MAX-паблишере включён `format="html"` для сохранения `b` и `i`;
- ссылки на уже опубликованные посты того же Telegram-канала переписываются на MAX permalink;
- Telegram-ссылки на другие каналы нормализуются к формату `https://t.me/...`;
- задержки публикации сделаны более консервативными, чтобы не бить API слишком часто;
- длинные HTML-посты теперь ужимаются до безопасного лимита MAX вместо `400 Bad Request`;
- пустые записи без текста и без media помечаются как skip для MAX и не попадают в бесконечные повторные попытки;
- подготовлен отдельный рабочий клон под новый канал;
- проект успешно прогнан на канале `andrey_i_vika` в диапазоне `4..158`;
- GitHub-репозиторий для этого клона создаётся отдельно после фиксации первого результата.
- добавлен отдельный интерактивный сборщик треков Яндекс Музыки для безопасного построения podcast link mapping.
- добавлен безопасный mapper для замены `mavestreambot` podcast-ссылок в `posts.db` только по подтверждённым совпадениям названий.

## Последний реальный прогон

Дата: `2026-03-24`

Команда:

```bash
python3 parser.py --channel andrey_i_vika --start 4 --end 158 --db posts.db
```

Итог:

- всего записей: `155`
- `success`: `143`
- `missing`: `12`
- `error`: `0`

Проверено выборочно:

- `4` → `none`
- `30` → `image`
- `98` → `image`
- `158` → `image`
