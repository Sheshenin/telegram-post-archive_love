from __future__ import annotations

import argparse
import logging
import random
import time

import httpx

from db import (
    connect_db,
    get_max_link_map,
    get_posts_for_max,
    mark_post_error,
    mark_post_published,
    mark_post_skipped_max,
    post_exists,
)
from max_client import MaxClient
from parser import DEFAULT_USER_AGENT, fetch_html, parse_post
from podcast_links import PodcastLinkRewriter, YandexMusicResolver
from publish_max import build_attachment, infer_channel_slug, prepare_text_for_max
from utils import build_fetch_url, build_post_url


LOGGER = logging.getLogger("watch_new_posts")
UPLOAD_READY_DELAY = {"image": 1.5, "video": 4.0}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Wait for new public Telegram posts, archive them into SQLite, and publish to MAX.",
    )
    parser.add_argument("--channel", required=True, help="Public Telegram channel name without @.")
    parser.add_argument("--db", required=True, help="SQLite database path.")
    parser.add_argument("--token", required=True, help="MAX Bot API token.")
    parser.add_argument("--chat", required=True, help="MAX chat or channel id.")
    parser.add_argument("--start-post-id", type=int, default=None, help="Force exact Telegram post id to wait for.")
    parser.add_argument("--poll-interval", type=float, default=30.0, help="How long to wait before rechecking.")
    parser.add_argument("--delay-min", type=float, default=3.0, help="Minimum delay between MAX publishes.")
    parser.add_argument("--delay-max", type=float, default=6.0, help="Maximum delay between MAX publishes.")
    parser.add_argument("--retries", type=int, default=3, help="Number of retries for Telegram/MAX requests.")
    parser.add_argument(
        "--tracks-json",
        default="yandex_album_tracks.json",
        help="Local Yandex track cache used for exact track URL replacements and fast title matches.",
    )
    parser.add_argument("--yandex-ssh-host", default=None, help="Optional SSH host for Yandex search fallback.")
    parser.add_argument("--yandex-ssh-user", default="root", help="SSH user for Yandex search fallback.")
    parser.add_argument("--yandex-ssh-key", default=None, help="SSH key path for Yandex search fallback.")
    return parser.parse_args()


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


def validate_args(args: argparse.Namespace) -> None:
    if args.poll_interval <= 0:
        raise SystemExit("--poll-interval must be positive")
    if args.retries < 1:
        raise SystemExit("--retries must be at least 1")
    if args.delay_min < 0 or args.delay_max < 0:
        raise SystemExit("--delay-min and --delay-max must be non-negative")
    if args.delay_min > args.delay_max:
        raise SystemExit("--delay-min must be less than or equal to --delay-max")


def get_next_post_id(connection, explicit_start: int | None) -> int:
    if explicit_start is not None:
        return explicit_start
    row = connection.execute("SELECT COALESCE(MAX(telegram_post_id), 0) AS max_post_id FROM posts").fetchone()
    return int(row["max_post_id"]) + 1


def sleep_random(delay_min: float, delay_max: float) -> None:
    time.sleep(random.uniform(delay_min, delay_max))


def publish_single_post(connection, client: MaxClient, chat_id: str, telegram_post_id: int) -> bool:
    posts = get_posts_for_max(connection, limit=1, single_post_id=telegram_post_id)
    if not posts:
        LOGGER.warning("Post %s skipped: no unpublished DB row found", telegram_post_id)
        return False

    post = posts[0]
    current_channel = infer_channel_slug(post.url)
    link_map = get_max_link_map(connection)
    text = prepare_text_for_max(post.text_html, current_channel=current_channel, link_map=link_map)
    attachments, media_url = build_attachment(post)

    if not text and not attachments:
        mark_post_skipped_max(connection, post.id, "Empty post content after formatting")
        LOGGER.warning("Post %s skipped", post.telegram_post_id)
        return True

    if attachments and media_url:
        token = client.upload_attachment(media_url, attachments[0]["type"])
        attachments[0]["payload"]["token"] = token
        time.sleep(UPLOAD_READY_DELAY.get(attachments[0]["type"], 1.5))
    else:
        attachments = []

    message_id, message_url = client.send_message(chat_id=chat_id, text=text, attachments=attachments)
    if not message_url:
        message_url = client.get_message_url(message_id)
    mark_post_published(connection, post.id, message_id, message_url)
    LOGGER.info("Post %s published", post.telegram_post_id)
    return True


def main() -> int:
    args = parse_args()
    configure_logging()
    validate_args(args)

    connection = connect_db(args.db)
    next_post_id = get_next_post_id(connection, args.start_post_id)
    LOGGER.info("Watching channel %s from post %s", args.channel, next_post_id)

    resolver = YandexMusicResolver(
        args.tracks_json,
        ssh_host=args.yandex_ssh_host,
        ssh_user=args.yandex_ssh_user,
        ssh_key_path=args.yandex_ssh_key,
    )
    link_rewriter = PodcastLinkRewriter(resolver)

    headers = {"User-Agent": DEFAULT_USER_AGENT}
    with httpx.Client(headers=headers, follow_redirects=True) as telegram_client, MaxClient(args.token) as max_client:
        while True:
            if post_exists(connection, next_post_id):
                next_post_id += 1
                continue

            canonical_url = build_post_url(args.channel, next_post_id)
            fetch_url = build_fetch_url(args.channel, next_post_id)

            try:
                html = fetch_html(telegram_client, fetch_url, args.retries)
                record = parse_post(args.channel, next_post_id, canonical_url, html)
            except Exception as exc:  # noqa: BLE001
                LOGGER.error("Post %s failed during fetch: %s", next_post_id, exc)
                time.sleep(args.poll_interval)
                continue

            if record.status == "missing":
                LOGGER.info("Post %s not published yet; waiting %.1fs", next_post_id, args.poll_interval)
                time.sleep(args.poll_interval)
                continue

            if record.status != "success":
                LOGGER.error("Post %s failed: %s", next_post_id, record.error)
                time.sleep(args.poll_interval)
                continue

            record = link_rewriter.rewrite_record(record)
            connection.execute(
                """
                INSERT INTO posts (
                    telegram_post_id, url, published_at, text_html, text_plain, has_media,
                    media_type, media_urls, raw_html, status, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.telegram_post_id,
                    record.url,
                    record.published_at,
                    record.text_html,
                    record.text_plain,
                    int(record.has_media),
                    record.media_type,
                    record.media_urls,
                    record.raw_html,
                    record.status,
                    record.error,
                ),
            )
            connection.commit()
            LOGGER.info("Post %s archived", next_post_id)

            published = False
            last_error = ""
            for attempt in range(1, args.retries + 1):
                try:
                    published = publish_single_post(connection, max_client, args.chat, next_post_id)
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = " ".join(str(exc).split())[:500]
                    if attempt >= args.retries:
                        break
                    time.sleep(2 ** max(attempt - 1, 0))

            if not published and last_error:
                row = connection.execute(
                    "SELECT id FROM posts WHERE telegram_post_id = ?",
                    (next_post_id,),
                ).fetchone()
                if row is not None:
                    mark_post_error(connection, int(row["id"]), last_error)
                LOGGER.error("Post %s failed: %s", next_post_id, last_error)

            next_post_id += 1
            sleep_random(args.delay_min, args.delay_max)


if __name__ == "__main__":
    raise SystemExit(main())
