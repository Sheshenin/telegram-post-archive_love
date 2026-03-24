from __future__ import annotations

import argparse
import logging
import time
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from db import connect_db, get_post_status, insert_post, post_exists
from models import PostRecord
from utils import (
    build_fetch_url,
    build_post_url,
    clip_error,
    compute_backoff,
    dumps_media_urls,
    extract_photo_url,
    get_text_plain,
    is_missing_post_page,
    make_absolute_media_urls,
    normalize_datetime,
    normalize_text_html,
    sleep_random,
)


LOGGER = logging.getLogger("telegram_post_archive")
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Archive public Telegram posts into SQLite.")
    parser.add_argument("--channel", required=True, help="Public Telegram channel name without @.")
    parser.add_argument("--start", required=True, type=int, help="Starting Telegram post id.")
    parser.add_argument(
        "--end",
        type=int,
        default=None,
        help="Ending Telegram post id. If omitted, parser runs until the current channel edge.",
    )
    parser.add_argument("--db", required=True, help="SQLite database path.")
    parser.add_argument("--delay-min", type=float, default=0.5, help="Minimum delay between requests.")
    parser.add_argument("--delay-max", type=float, default=1.5, help="Maximum delay between requests.")
    parser.add_argument("--retries", type=int, default=3, help="Number of HTTP retries per post.")
    parser.add_argument("--limit", type=int, default=None, help="Process only N new posts.")
    parser.add_argument(
        "--latest-gap-threshold",
        type=int,
        default=20,
        help="When --end is omitted, stop after this many consecutive missing posts.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not write results into SQLite.")
    return parser.parse_args()


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


def fetch_html(
    client: httpx.Client,
    url: str,
    retries: int,
) -> str:
    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            response = client.get(url, timeout=10.0)
            response.raise_for_status()
            return response.text
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt >= retries:
                break
            time.sleep(compute_backoff(attempt))

    assert last_error is not None
    raise last_error


def parse_post(channel: str, post_id: int, canonical_url: str, html: str) -> PostRecord:
    soup = BeautifulSoup(html, "html.parser")
    if is_missing_post_page(soup):
        return PostRecord(
            telegram_post_id=post_id,
            url=canonical_url,
            published_at=None,
            text_html=None,
            text_plain=None,
            has_media=False,
            media_type="none",
            media_urls=dumps_media_urls([]),
            raw_html=html,
            status="missing",
            error=None,
        )

    message = soup.select_one(f'.tgme_widget_message[data-post="{channel}/{post_id}"]')
    if message is None:
        return PostRecord(
            telegram_post_id=post_id,
            url=canonical_url,
            published_at=None,
            text_html=None,
            text_plain=None,
            has_media=False,
            media_type="none",
            media_urls=dumps_media_urls([]),
            raw_html=html,
            status="missing",
            error=None,
        )

    text_block = message.select_one(".tgme_widget_message_text")
    time_tag = message.select_one("time[datetime]")
    photos = []
    for photo in message.select(".tgme_widget_message_photo_wrap"):
        photo_url = extract_photo_url(photo.get("style"))
        if photo_url:
            photos.append(photo_url)

    video_sources = []
    for source in message.select("video[src], video source[src]"):
        src = source.get("src")
        if src:
            video_sources.append(src)

    media_urls = make_absolute_media_urls(video_sources or photos, canonical_url)
    if video_sources:
        media_type = "video"
    elif len(photos) > 1:
        media_type = "album"
    elif len(photos) == 1:
        media_type = "image"
    else:
        media_type = "none"

    return PostRecord(
        telegram_post_id=post_id,
        url=canonical_url,
        published_at=normalize_datetime(time_tag.get("datetime") if time_tag else None),
        text_html=normalize_text_html(text_block),
        text_plain=get_text_plain(text_block),
        has_media=bool(media_urls),
        media_type=media_type,
        media_urls=dumps_media_urls(media_urls),
        raw_html=html,
        status="success",
        error=None,
    )


def make_error_record(post_id: int, url: str, error: Exception) -> PostRecord:
    return PostRecord(
        telegram_post_id=post_id,
        url=url,
        published_at=None,
        text_html=None,
        text_plain=None,
        has_media=False,
        media_type="none",
        media_urls=dumps_media_urls([]),
        raw_html=None,
        status="error",
        error=clip_error(str(error)),
    )


def should_stop(processed_count: int, limit: Optional[int]) -> bool:
    return limit is not None and processed_count >= limit


def should_stop_on_latest(missing_streak: int, threshold: int, explicit_end: Optional[int]) -> bool:
    return explicit_end is None and missing_streak >= threshold


def iter_post_ids(start: int, end: Optional[int]):
    if end is None:
        post_id = start
        while True:
            yield post_id
            post_id += 1
    else:
        yield from range(start, end + 1)


def main() -> int:
    args = parse_args()
    configure_logging()

    if args.end is not None and args.start > args.end:
        raise SystemExit("--start must be less than or equal to --end")
    if args.delay_min < 0 or args.delay_max < 0:
        raise SystemExit("--delay-min and --delay-max must be non-negative")
    if args.delay_min > args.delay_max:
        raise SystemExit("--delay-min must be less than or equal to --delay-max")
    if args.retries < 1:
        raise SystemExit("--retries must be at least 1")
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be at least 1")
    if args.latest_gap_threshold < 1:
        raise SystemExit("--latest-gap-threshold must be at least 1")

    connection = connect_db(args.db)
    processed_count = 0
    missing_streak = 0

    headers = {"User-Agent": DEFAULT_USER_AGENT}
    with httpx.Client(headers=headers, follow_redirects=True) as client:
        for post_id in iter_post_ids(args.start, args.end):
            if should_stop(processed_count, args.limit):
                LOGGER.info("Reached processing limit: %s", args.limit)
                break

            if not args.dry_run and post_exists(connection, post_id):
                existing_status = get_post_status(connection, post_id)
                LOGGER.info("Post %s skipped: already in DB", post_id)
                if existing_status == "missing":
                    missing_streak += 1
                else:
                    missing_streak = 0

                if should_stop_on_latest(missing_streak, args.latest_gap_threshold, args.end):
                    LOGGER.info(
                        "Stopped at current channel edge after %s consecutive missing posts",
                        missing_streak,
                    )
                    break
                continue

            canonical_url = build_post_url(args.channel, post_id)
            fetch_url = build_fetch_url(args.channel, post_id)

            try:
                html = fetch_html(client, fetch_url, args.retries)
                record = parse_post(args.channel, post_id, canonical_url, html)
            except Exception as exc:  # noqa: BLE001
                record = make_error_record(post_id, canonical_url, exc)

            if record.status == "success":
                LOGGER.info("Post %s processed", post_id)
                missing_streak = 0
            elif record.status == "missing":
                LOGGER.warning("Post %s missing", post_id)
                missing_streak += 1
            else:
                LOGGER.error("Post %s failed: %s", post_id, record.error)
                missing_streak = 0

            if not args.dry_run:
                insert_post(connection, record)

            processed_count += 1

            if should_stop_on_latest(missing_streak, args.latest_gap_threshold, args.end):
                LOGGER.info(
                    "Stopped at current channel edge after %s consecutive missing posts",
                    missing_streak,
                )
                break

            if not should_stop(processed_count, args.limit):
                sleep_random(args.delay_min, args.delay_max)

    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
