# Telegram Post Archive — Plan

## Статус

`cloned from telegram-post-archive for channel andrey_i_vika, first real run completed` — 2026-03-24

## Цель

Собрать локальный SQLite-архив публичного Telegram-канала `@andrey_i_vika` по прямым ссылкам на посты и обеспечить безопасный перезапуск без потери прогресса.

## Функциональный объём

- обход диапазона `post_id` через CLI;
- обход от заданного `start` до актуального края канала, если `end` не указан;
- пропуск уже обработанных записей;
- загрузка HTML по `https://t.me/<channel>/<post_id>`;
- разбор текста, даты и media metadata;
- сохранение результата в SQLite;
- публикация в канал MAX из SQLite без дублирования;
- статусная модель `success` / `missing` / `error`;
- retry с exponential backoff;
- dry-run и limit.

## Принятые решения

- использовать `httpx`, без Selenium и без браузера;
- хранить `media_urls` в JSON-строке;
- для `missing` и `error` создавать запись в SQLite, чтобы не терять прогресс;
- в режиме без `--end` останавливаться после порога подряд идущих `missing`, чтобы не требовать ручного поиска последнего `post_id`;
- считать пост отсутствующим, если на странице нет Telegram widget message;
- HTML очищать через `BeautifulSoup` с allowlist тегов `b`, `i`, `a`, `br`.
- для MAX-publisher использовать только первое media-вложение в посте.

## MVP checklist

- [x] Склонировать рабочую кодовую базу из `telegram-post-archive`
- [x] Сохранить схему SQLite, CLI и MAX publisher
- [x] Подготовить документацию под новый канал
- [x] Определить реальный диапазон постов канала `@andrey_i_vika` (`4..158`)
- [x] Выполнить первый реальный прогон архивации
- [x] Проверить содержимое `posts.db` на реальных постах
- [ ] Прогнать dry-run публикации в MAX
- [ ] Прогнать реальную публикацию `--limit 5`
- [x] Добавить интерактивный сборщик треков Яндекс Музыки для безопасного маппинга podcast-ссылок
- [x] Добавить безопасный mapper для замены podcast-ссылок по совпадению названий, а не по порядку
- [x] Собрать полный Yandex playlist `Sheshenin/1001` и заменить все podcast-ссылки в архивной базе
- [x] Добавить live watcher без cron для ожидания новых постов, записи в SQLite и отправки в MAX
- [x] Переключить live-подмену podcast-ссылок на поиск по названию эпизода из самого Telegram-поста
- [x] Добавить exact `title -> Yandex track URL` resolver для новых выпусков
- [x] Зафиксировать правило, что не каждый post с `mavestreambot` ссылкой является episode-post
- [x] Подготовить `systemd` unit для постоянного daemon/autostart запуска watcher
- [x] Установить и включить `telegram-post-archive-love-watcher.service`
- [x] Исправить recovery-path для archived-but-unpublished записей watcher
- [x] Перевести MAX env на numeric `chat_id` из `GET /chats`
- [x] Починить SSH fallback live-поиска новых эпизодов в Яндекс Музыке
- [ ] Проверить `watch_new_posts.py` на реальном новом посте канала
- [ ] Проверить fallback-поиск в Yandex search через альтернативный SSH-host на реальном новом выпуске
- [ ] Проверить автозапуск watcher после reboot

## Следующий шаг

- создать отдельный GitHub-репозиторий для этого клона;
- выполнить первый dry-run публикации в MAX;
- при необходимости добавить инкрементальный режим обновления под новые посты канала.
- после интерактивного сбора треков собрать точный mapping `lovebusiness_<n> -> yandex track URL`.
- после получения `yandex_album_tracks.json` выполнить preview-отчёт и затем `--apply` только по подтверждённым match.
- при необходимости использовать уже собранный `yandex_album_tracks.json` для повторной замены в новых инкрементальных записях.
- для ongoing-режима не использовать cron: держать живой процесс ожидания следующего `post_id`.
- для live-режима брать название эпизода прямо из Telegram-поста и искать Yandex track URL по этому названию.
- episode-post определять только по явному anchor-анонсу `Эпизод N...`, а не по любой podcast-ссылке.
- для постоянного режима запускать watcher как `systemd`-сервис с внешним env-файлом, не храня секреты в git.
