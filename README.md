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
  watch_new_posts.py
  podcast_links.py
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

Живой монитор новых постов:

```bash
python watch_new_posts.py \
  --channel andrey_i_vika \
  --db posts.db \
  --token YOUR_TOKEN \
  --chat CHAT_ID \
  --poll-interval 30 \
  --yandex-ssh-host YOUR_ALT_HOST \
  --yandex-ssh-key /path/to/key
```

Запуск как `systemd`-демон:

```bash
sudo cp deploy/systemd/telegram-post-archive-love-watcher.service /etc/systemd/system/
sudo tee /etc/telegram-post-archive-love-watcher.env >/dev/null <<'EOF'
CHANNEL=andrey_i_vika
DB_PATH=/home/deploy/app/telegram-post-archive_love/posts.db
MAX_TOKEN=YOUR_TOKEN
MAX_CHAT_ID=id772576559690_biz
POLL_INTERVAL=30
DELAY_MIN=3.0
DELAY_MAX=6.0
RETRIES=3
YANDEX_SSH_HOST=31.130.133.235
YANDEX_SSH_KEY=/root/.ssh/id_ed25519twc
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now telegram-post-archive-love-watcher.service
sudo systemctl status telegram-post-archive-love-watcher.service
```

Текущее состояние на `2026-03-25`:

- сервис установлен в `/etc/systemd/system/telegram-post-archive-love-watcher.service`;
- автозапуск включён;
- runtime env хранится в `/etc/telegram-post-archive-love-watcher.env`;
- лог пишется в `watch_new_posts.log`.

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

## Живой монитор новых постов

Для инкрементальной работы без cron в проект добавлен отдельный watcher:

- ждёт ровно следующий `post_id` канала, а не стартует по расписанию;
- как только пост появляется в `https://t.me/s/<channel>/<next_id>`, архивирует его в `posts.db`;
- сразу после этого публикует его в MAX;
- при рестарте продолжает с первого ещё не архивированного `post_id`.

Важно:

- не каждый пост с `mavestreambot` ссылкой считается выпуском;
- live-контур считает пост выпуском только если в `mavestreambot` anchor есть явный анонс вида `Эпизод N...`;
- если в anchor есть только `Эпизод N`, а название стоит сразу после ссылки, оно добирается из ближайших inline-соседей до первого `<br>`;
- обычные CTA-посты вроде `с первого эпизода` не считаются выпуском и не триггерят Yandex resolver;
- локальный кэш `yandex_album_tracks.json` используется для быстрых exact-match по названию;
- если точного match в кэше нет, watcher ищет трек по названию в Яндекс Музыке;
- если основной сервер Яндекс блокирует по региону, watcher может искать через альтернативный SSH-host;
- если точный track URL не найден, watcher не выдумывает ссылку и оставляет исходную Telegram-ссылку без подмены.
- для постоянной работы после перезагрузки watcher можно запускать как `systemd`-сервис `telegram-post-archive-love-watcher.service` через внешний env-файл с секретами.

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
- вычисляет целевой эпизод для каждого конкретного вхождения ссылки внутри поста;
- использует title-based match по локальному кэшу треков;
- при необходимости live-контур может искать точный `track URL` по названию эпизода в Яндекс Музыке.
- дополнительно проверяет явные заголовки эпизодов в anchor text;
- сохраняет отчёт с `resolved` и `unresolved` кейсами по каждой ссылке;
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
- подтверждён публичный плейлист `https://music.yandex.ru/iframe/playlist/Sheshenin/1001` с полными `50` треками;
- собран `yandex_album_tracks.json`/`csv` из API плейлиста;
- все podcast-ссылки `mavestreambot` в `posts.db` заменены на `music.yandex.ru/album/36214929/track/...`.
- для live-режима добавлен `watch_new_posts.py`, который ждёт новые посты без cron и ищет Yandex track URL по названию эпизода из самого Telegram-поста.

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
