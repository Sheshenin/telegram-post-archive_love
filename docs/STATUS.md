# Статус проекта

## Текущий статус

Проект создан как отдельный Python CLI в `/home/deploy/app/telegram-post-archive_love` на основе рабочего `telegram-post-archive` и подготовлен к первому запуску против публичного канала `@andrey_i_vika`.

Текущий рабочий статус на `2026-03-24`:

- код и документация склонированы;
- локальная база `posts.db` создана первым controlled-run;
- зафиксирован диапазон первого controlled-run: `4..158`;
- GitHub-репозиторий для этого клона создан.
- добавлен интерактивный Playwright-сценарий для сбора названий и ссылок треков с альбома Яндекс Музыки.

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
