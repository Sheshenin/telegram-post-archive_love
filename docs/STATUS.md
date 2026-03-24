# Статус проекта

## Текущий статус

Проект создан как отдельный Python CLI в `/home/deploy/app/telegram-post-archive_love` на основе рабочего `telegram-post-archive` и подготовлен к первому запуску против публичного канала `@andrey_i_vika`.

Текущий рабочий статус на `2026-03-24`:

- код и документация склонированы;
- локальная база `posts.db` создана первым controlled-run;
- зафиксирован диапазон первого controlled-run: `4..158`;
- GitHub-репозиторий для этого клона создан.
- добавлен интерактивный Playwright-сценарий для сбора названий и ссылок треков с альбома Яндекс Музыки.
- добавлен безопасный `podcast_link_mapper.py` для title-based замены podcast-ссылок в `posts.db`.
- подтверждён и использован публичный плейлист `Sheshenin/1001` как источник всех `50` Yandex Music треков.
- добавлен live watcher без cron для ожидания новых постов и немедленной отправки в MAX.
- добавлен live resolver `title -> Yandex track URL` для новых podcast-постов.
- подготовлен и установлен `systemd` unit для daemon/autostart режима watcher после перезагрузки.

Первый реальный прогон выполнен `2026-03-24`.

## Что уже реализовано

- SQLite schema `posts` с индексами;
- HTTP fetcher с timeout, retry и случайной задержкой между запросами;
- fetch через `https://t.me/s/...` для получения реального HTML истории, а не embed shell;
- parser Telegram widget HTML для текста, даты, photo/video media;
- классификация media: `none`, `image`, `album`, `video`;
- миграция колонок публикации в MAX внутри `posts`;
- formatter для очистки и нормализации HTML под MAX;
- MAX client для upload/send message;
- CLI `publish_max.py` с `dry-run`, `limit`, `post-id`, retry и защитой от дублей;
- rewrite ссылок на уже опубликованные посты того же канала в MAX permalink;
- нормализация Telegram-ссылок на другие каналы к виду `https://t.me/...`;
- передача `format=\"html\"` для сохранения bold/italic в MAX;
- более мягкий default rate limit для снижения риска блокировок;
- защита от `400 Bad Request` на длинных постах через безопасную HTML-обрезку под лимит MAX;
- skip пустых `success`-записей без текста и media, чтобы они не повторялись на каждом запуске;
- очистка `text_html` с сохранением только базовых inline-тегов;
- авто-режим прохода от стартового `post_id` до текущего края канала без ручного `--end`;
- режимы `dry-run` и `limit`;
- логирование формата `[LEVEL] message`;
- `.gitignore` и базовая документация для отдельного git-репозитория.
- `yandex_album_scraper.py` для интерактивного получения `track_id`, `track_url`, `title` с альбома Яндекс Музыки.
- `podcast_link_mapper.py` для безопасного сопоставления `lovebusiness...` ссылок с Yandex Music track URL.
- `watch_new_posts.py` для бесконечного ожидания следующего поста, записи в SQLite и немедленной отправки в MAX.
- `podcast_links.py` для live-подмены `mavestreambot` ссылок по названию эпизода из самого Telegram-поста.
- `podcast_links.py` умеет искать точный `music.yandex.ru/album/.../track/...` в Yandex search.
- `podcast_links.py` отличает реальные episode-posts от обычных CTA-постов с podcast-ссылкой.
- `deploy/systemd/telegram-post-archive-love-watcher.service` описывает постоянный запуск watcher через внешний env-файл `/etc/telegram-post-archive-love-watcher.env`.
- `telegram-post-archive-love-watcher.service` уже включён через `systemctl enable --now` и находится в состоянии `active (running)`.

## Git-статус

- проект оформлен как отдельный репозиторий `telegram-post-archive_love`;
- локальная ветка `main` привязана к `origin`;
- репозиторий опубликован в GitHub: `https://github.com/Sheshenin/telegram-post-archive_love`.

## Результат реального запуска

Команда:

```bash
python3 parser.py --channel andrey_i_vika --start 4 --end 158 --db posts.db
```

Итог по `posts.db`:

- всего записей: `155`
- `success`: `143`
- `missing`: `12`
- `error`: `0`

Контрольные записи:

- `4` → `none`
- `30` → `image`
- `98` → `image`
- `158` → `image`

## Что ещё не проверено

- поведение на редких или нестандартных типах разметки Telegram;
- полнота allowlist для специфических Telegram тегов вроде `blockquote`, `tg-spoiler`, `tg-emoji`;
- долгий инкрементальный запуск поверх уже существующей базы после появления новых постов.
- реальный MAX API flow против живого токена и чата;
- точный формат ответа `/uploads` и `/messages` на production-аккаунте MAX.
- канонический public permalink MAX для опубликованных сообщений, если API не отдаёт `url`.
- полный интерактивный прогон `yandex_album_scraper.py` против живой captcha-сессии Yandex Music.
- фактический preview/apply прогон `podcast_link_mapper.py` против реального `yandex_album_tracks.json`.
- повторная проверка базы после замены ссылок и отсутствие оставшихся `mavestreambot` podcast URL.
- долгий live-run `watch_new_posts.py` против реально появляющихся новых постов канала.
- автоматический `title -> Yandex track URL` поиск для абсолютно нового выпуска, которого ещё нет в локальном `yandex_album_tracks.json`.
- реальная проверка `systemd`-автозапуска watcher после полного reboot.

## Ближайшая проверка

Запуск:

```bash
cd /home/deploy/app/telegram-post-archive_love
python parser.py --channel andrey_i_vika --start 4 --end 158 --db posts.db
```

После запуска проверить:

- сохранение `<b>` и `<i>` в `text_html`;
- корректность `published_at`;
- корректность `media_type` и `media_urls`;
- корректность авто-остановки на крае канала;
- наличие записей `missing` для несуществующих постов.

Для интерактивного сбора треков Яндекс Музыки:

```bash
python yandex_album_scraper.py \
  --album-url "https://music.yandex.ru/album/36214929?utm_source=web&utm_medium=copy_link"
```

После запуска проверить:

- открывается ли headed Chromium;
- удаётся ли пройти login/captcha;
- сохраняются ли `yandex_album_tracks.json` и `yandex_album_tracks.csv`;
- есть ли в результатах `track_id`, `track_url`, `title` без дублей.

Для безопасной замены podcast-ссылок:

```bash
python podcast_link_mapper.py \
  --db posts.db \
  --tracks-json yandex_album_tracks.json \
  --report-json podcast_link_mapping_report.json
```

После запуска проверить:

- сколько `resolved` и `unresolved` ключей попало в отчёт;
- нет ли ambiguous match по названиям;
- перед `--apply` устраивает ли отчёт по ранним edge-case ссылкам.

## Фактический результат замены podcast-ссылок

Источник треков:

- публичный плейлист `https://music.yandex.ru/iframe/playlist/Sheshenin/1001`
- подтверждённый размер: `50` треков
- порядок обратный относительно номеров эпизодов:
  `эпизод 1 -> последний трек`, `эпизод 50 -> первый трек`

Результат apply-прогона:

- `resolved podcast links`: `102`
- `unresolved podcast links`: `0`
- обновлено постов: `50`
- осталось Telegram podcast-ссылок `mavestreambot`: `0`
- постов с Yandex track URL: `50`

Локальный backup перед заменой:

- `posts_backup_before_podcast_links_20260324_190626.db`

## Live monitoring

Новая целевая схема для ongoing-режима:

- не cron;
- не batch range parser;
- watcher ждёт публикации ровно следующего `post_id` канала;
- как только пост появляется, он сразу архивируется в `posts.db` и уходит в MAX;
- название эпизода берётся из самого Telegram-поста, а не из Mave или плейлиста.

Правило определения episode-post:

- наличие любой `mavestreambot` ссылки само по себе недостаточно;
- выпуском считается только пост, где есть явный anchor-анонс `Эпизод N...`;
- если название стоит не внутри anchor, а сразу после него, оно добирается из ближайших inline-соседей;
- CTA-посты вроде `Начните с первого эпизода` не считаются выпуском и не запускают поиск в Яндекс Музыке.

Текущий нюанс:

- на основном сервере Yandex search может упираться в region block;
- для этого live-контур поддерживает поиск через альтернативный SSH-host;
- если точный матч в Yandex search не найден, watcher не подставляет фиктивную ссылку.

## Daemon mode

Для постоянной работы watcher подготовлен под `systemd`:

- unit-шаблон лежит в `deploy/systemd/telegram-post-archive-love-watcher.service`;
- секреты вынесены из git в `/etc/telegram-post-archive-love-watcher.env`;
- рабочий лог сервиса пишется в `/home/deploy/app/telegram-post-archive_love/watch_new_posts.log`;
- целевой режим: `Restart=always`, `WantedBy=multi-user.target`.
- фактически установленный сервис уже ждёт следующий пост `159`.
