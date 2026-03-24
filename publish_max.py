from __future__ import annotations

import argparse
import logging
import random
import time
from urllib.parse import urlparse

from bs4 import BeautifulSoup, NavigableString, Tag

from db import (
    connect_db,
    get_max_link_map,
    get_posts_for_max,
    get_published_posts_for_url_sync,
    mark_post_error,
    mark_post_published,
    mark_post_skipped_max,
    update_max_message_url,
)
from formatter import format_text_html
from max_client import MaxClient


LOGGER = logging.getLogger("publish_max")
UPLOAD_READY_DELAY = {"image": 1.5, "video": 4.0}
MAX_TEXT_LENGTH = 3900


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish archived posts from SQLite to MAX.")
    parser.add_argument("--db", required=True, help="SQLite database path.")
    parser.add_argument("--token", required=True, help="MAX Bot API token.")
    parser.add_argument("--chat", required=True, help="MAX chat or channel id.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum number of posts to publish.")
    parser.add_argument("--delay-min", type=float, default=3.0, help="Minimum delay between requests.")
    parser.add_argument("--delay-max", type=float, default=6.0, help="Maximum delay between requests.")
    parser.add_argument("--retries", type=int, default=3, help="Number of retries per post.")
    parser.add_argument("--post-id", type=int, default=None, help="Publish only one Telegram post id.")
    parser.add_argument(
        "--sync-message-urls",
        action="store_true",
        help="Refresh real MAX permalinks for already published posts via GET /messages/{mid}.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not send requests to MAX.")
    return parser.parse_args()


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


def compute_backoff(attempt: int) -> float:
    return 2 ** max(attempt - 1, 0)


def sleep_random(delay_min: float, delay_max: float) -> None:
    time.sleep(random.uniform(delay_min, delay_max))


def validate_args(args: argparse.Namespace) -> None:
    if args.limit < 1:
        raise SystemExit("--limit must be at least 1")
    if args.retries < 1:
        raise SystemExit("--retries must be at least 1")
    if args.delay_min < 0 or args.delay_max < 0:
        raise SystemExit("--delay-min and --delay-max must be non-negative")
    if args.delay_min > args.delay_max:
        raise SystemExit("--delay-min must be less than or equal to --delay-max")


def build_attachment(post) -> tuple[list[dict[str, str]], str | None]:
    if not post.media_urls:
        return [], None

    media_url = post.media_urls[0]
    if post.media_type == "video":
        attachment_type = "video"
    else:
        attachment_type = "image"

    return [{"type": attachment_type, "payload": {"token": ""}}], media_url


def prepare_text_for_max(text_html: str | None, current_channel: str | None, link_map: dict[int, str]) -> str:
    text = format_text_html(text_html, current_channel=current_channel, link_map=link_map)
    if not text:
        return ""
    if len(text) <= MAX_TEXT_LENGTH:
        return text
    return truncate_html(text, MAX_TEXT_LENGTH)


def truncate_html(html: str, max_length: int) -> str:
    soup = BeautifulSoup(html, "html.parser")
    parts: list[str] = []
    remaining = max_length

    def open_tag(tag: Tag) -> str:
        if tag.name == "a":
            href = tag.get("href", "")
            return f'<a href="{href}">'
        return f"<{tag.name}>"

    def close_tag(tag: Tag) -> str:
        return f"</{tag.name}>"

    def append_node(node) -> None:
        nonlocal remaining
        if remaining <= 0:
            return

        if isinstance(node, NavigableString):
            text = str(node)
            if not text:
                return
            chunk = text[:remaining]
            parts.append(chunk)
            remaining -= len(chunk)
            return

        if not isinstance(node, Tag):
            return

        start = open_tag(node)
        end = close_tag(node)
        overhead = len(start) + len(end)
        if overhead > remaining:
            return

        parts.append(start)
        remaining -= len(start)
        for child in node.contents:
            if remaining <= len(end):
                break
            append_node(child)
        if len(end) <= remaining:
            parts.append(end)
            remaining -= len(end)

    for child in soup.contents:
        if remaining <= 0:
            break
        append_node(child)

    return "".join(parts)


def publish_post(client: MaxClient, chat_id: str, post) -> str:
    current_channel = infer_channel_slug(post.url)
    text = prepare_text_for_max(post.text_html, current_channel=current_channel, link_map=publish_post.link_map)
    attachments, media_url = build_attachment(post)

    if not text and not attachments:
        raise ValueError("Empty post content after formatting")

    if attachments and media_url:
        token = client.upload_attachment(media_url, attachments[0]["type"])
        attachments[0]["payload"]["token"] = token
        time.sleep(UPLOAD_READY_DELAY.get(attachments[0]["type"], 1.5))
    else:
        attachments = []

    return client.send_message(chat_id=chat_id, text=text, attachments=attachments)


publish_post.link_map = {}


def infer_channel_slug(post_url: str) -> str | None:
    path_parts = [part for part in urlparse(post_url).path.split("/") if part]
    if len(path_parts) >= 2:
        return path_parts[0]
    return None


def main() -> int:
    args = parse_args()
    configure_logging()
    validate_args(args)

    connection = connect_db(args.db)
    if args.sync_message_urls:
        with MaxClient(args.token) as client:
            sync_message_urls(connection, client)
        connection.close()
        return 0

    posts = get_posts_for_max(connection, limit=args.limit, single_post_id=args.post_id)
    publish_post.link_map = get_max_link_map(connection)

    if not posts:
        LOGGER.warning("No posts to publish")
        connection.close()
        return 0

    with MaxClient(args.token) as client:
        for index, post in enumerate(posts):
            if args.dry_run:
                LOGGER.info("Post %s published", post.telegram_post_id)
                continue

            success = False
            last_error = ""

            for attempt in range(1, args.retries + 1):
                try:
                    message_id, message_url = publish_post(client, args.chat, post)
                    if not message_url:
                        message_url = client.get_message_url(message_id)
                    mark_post_published(connection, post.id, message_id, message_url)
                    if message_url:
                        publish_post.link_map[post.telegram_post_id] = message_url
                    LOGGER.info("Post %s published", post.telegram_post_id)
                    success = True
                    break
                except ValueError as exc:
                    last_error = " ".join(str(exc).split())[:500]
                    if last_error == "Empty post content after formatting":
                        mark_post_skipped_max(connection, post.id, last_error)
                        LOGGER.warning("Post %s skipped", post.telegram_post_id)
                        success = True
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = " ".join(str(exc).split())[:500]
                    if attempt >= args.retries:
                        break
                    time.sleep(compute_backoff(attempt))

            if not success:
                mark_post_error(connection, post.id, last_error)
                LOGGER.error("Post %s failed: %s", post.telegram_post_id, last_error)

            if index < len(posts) - 1:
                sleep_random(args.delay_min, args.delay_max)

    connection.close()
    return 0


def sync_message_urls(connection, client: MaxClient) -> None:
    rows = get_published_posts_for_url_sync(connection)
    for row in rows:
        message_url = client.get_message_url(str(row["max_message_id"]))
        if not message_url:
            LOGGER.warning("Post %s skipped: MAX returned no public URL", row["telegram_post_id"])
            continue
        if row["max_message_url"] == message_url:
            LOGGER.info("Post %s skipped", row["telegram_post_id"])
            continue
        update_max_message_url(connection, int(row["id"]), message_url)
        LOGGER.info("Post %s permalink synced", row["telegram_post_id"])


if __name__ == "__main__":
    raise SystemExit(main())
