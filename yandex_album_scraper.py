from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


LOGGER = logging.getLogger("yandex_album_scraper")
TRACK_RE = re.compile(r"/album/(?P<album_id>\d+)/track/(?P<track_id>\d+)")
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Yandex Music album track links and titles.")
    parser.add_argument("--album-url", required=True, help="Yandex Music album URL.")
    parser.add_argument(
        "--output-json",
        default="yandex_album_tracks.json",
        help="Where to save collected tracks as JSON.",
    )
    parser.add_argument(
        "--output-csv",
        default="yandex_album_tracks.csv",
        help="Where to save collected tracks as CSV.",
    )
    parser.add_argument(
        "--user-data-dir",
        default=".playwright-yandex-profile",
        help="Persistent browser profile directory.",
    )
    parser.add_argument(
        "--proxy-server",
        default=None,
        help="Optional proxy server, e.g. socks5://127.0.0.1:17890.",
    )
    parser.add_argument(
        "--expected-min",
        type=int,
        default=40,
        help="Warn if fewer than this many tracks were collected.",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=60000,
        help="Page interaction timeout in milliseconds.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run headless. Default is headed to allow manual captcha/login.",
    )
    return parser.parse_args()


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


def strip_query(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def ensure_parent(path: Path) -> None:
    if path.parent != Path():
        path.parent.mkdir(parents=True, exist_ok=True)


def is_captcha_page(page: Any) -> bool:
    current_url = page.url.lower()
    content = page.content().lower()
    return "showcaptcha" in current_url or "smartcaptcha" in content or "вы не робот" in content


def wait_for_manual_confirmation(page: Any) -> None:
    LOGGER.warning("Manual action required in the opened browser window.")
    LOGGER.warning("Complete login/captcha in Yandex Music, then return to this terminal.")
    if sys.stdin.isatty():
        input("Press Enter after the album page is fully visible with the track list...")
    else:
        while is_captcha_page(page):
            LOGGER.info("Waiting for captcha to be solved...")
            time.sleep(5)


def scroll_album(page: Any) -> None:
    stable_rounds = 0
    previous_height = -1
    previous_count = -1

    while stable_rounds < 3:
        count = page.locator('a[href*="/track/"]').count()
        height = page.evaluate("document.body.scrollHeight")
        page.mouse.wheel(0, 3000)
        page.wait_for_timeout(1200)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1200)
        if count == previous_count and height == previous_height:
            stable_rounds += 1
        else:
            stable_rounds = 0
        previous_count = count
        previous_height = height


def collect_tracks(page: Any) -> list[dict[str, Any]]:
    rows = page.evaluate(
        """
        () => {
          const result = [];
          for (const anchor of document.querySelectorAll('a[href*="/track/"]')) {
            const href = anchor.href || anchor.getAttribute('href') || '';
            const text = (anchor.textContent || '').replace(/\\s+/g, ' ').trim();
            const aria = (anchor.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim();
            const title = text || aria;
            if (!href || !title) {
              continue;
            }
            result.push({ href, title });
          }
          return result;
        }
        """
    )
    by_track_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        normalized_url = strip_query(row["href"])
        match = TRACK_RE.search(normalized_url)
        if not match:
            continue
        track_id = match.group("track_id")
        existing = by_track_id.get(track_id)
        candidate = {
            "album_id": match.group("album_id"),
            "track_id": track_id,
            "track_url": normalized_url,
            "title": row["title"].strip(),
        }
        if existing is None or len(candidate["title"]) > len(existing["title"]):
            by_track_id[track_id] = candidate
    return sorted(by_track_id.values(), key=lambda item: int(item["track_id"]))


def write_outputs(tracks: list[dict[str, Any]], json_path: Path, csv_path: Path) -> None:
    ensure_parent(json_path)
    ensure_parent(csv_path)

    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(tracks, handle, ensure_ascii=False, indent=2)

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["album_id", "track_id", "track_url", "title"])
        writer.writeheader()
        writer.writerows(tracks)


def main() -> int:
    args = parse_args()
    configure_logging()

    output_json = Path(args.output_json)
    output_csv = Path(args.output_csv)
    user_data_dir = Path(args.user_data_dir)

    launch_options: dict[str, Any] = {
        "headless": args.headless,
        "user_agent": DEFAULT_USER_AGENT,
    }
    if args.proxy_server:
        launch_options["proxy"] = {"server": args.proxy_server}

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(str(user_data_dir), **launch_options)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(args.timeout_ms)
            LOGGER.info("Opening album: %s", args.album_url)
            page.goto(args.album_url, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)

            if is_captcha_page(page):
                wait_for_manual_confirmation(page)
                page.wait_for_timeout(2000)

            try:
                page.wait_for_selector('a[href*="/track/"]', timeout=args.timeout_ms)
            except PlaywrightTimeoutError:
                if is_captcha_page(page):
                    raise SystemExit(
                        "Track list is still blocked by captcha. Open the headed browser and solve it first."
                    ) from None
                raise SystemExit("Track links did not appear on the album page.") from None

            scroll_album(page)
            tracks = collect_tracks(page)
            write_outputs(tracks, output_json, output_csv)

            LOGGER.info("Collected tracks: %s", len(tracks))
            LOGGER.info("Saved JSON: %s", output_json)
            LOGGER.info("Saved CSV: %s", output_csv)
            if tracks:
                LOGGER.info("First track: %s", tracks[0]["title"])
                LOGGER.info("Last track: %s", tracks[-1]["title"])
            if len(tracks) < args.expected_min:
                LOGGER.warning(
                    "Collected only %s tracks, below expected minimum %s",
                    len(tracks),
                    args.expected_min,
                )
        finally:
            context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
