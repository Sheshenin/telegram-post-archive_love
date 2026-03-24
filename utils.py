from __future__ import annotations

import json
import random
import re
import time
from typing import Iterable, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from bs4.element import Tag


ALLOWED_TAGS = {"b", "i", "a", "br"}
PHOTO_URL_RE = re.compile(r"url\((['\"]?)(.*?)\1\)")


def build_post_url(channel: str, post_id: int) -> str:
    return f"https://t.me/{channel}/{post_id}"


def build_fetch_url(channel: str, post_id: int) -> str:
    return f"https://t.me/s/{channel}/{post_id}"


def compute_backoff(attempt: int, base_delay: float = 1.0, max_delay: float = 30.0) -> float:
    return min(max_delay, base_delay * (2 ** max(attempt - 1, 0)))


def sleep_random(delay_min: float, delay_max: float) -> None:
    time.sleep(random.uniform(delay_min, delay_max))


def normalize_datetime(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return value.strip()


def sanitize_html_fragment(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html, "html.parser")

    for tag in list(soup.find_all(True)):
        if tag.name not in ALLOWED_TAGS:
            tag.unwrap()
            continue

        if tag.name == "a":
            href = tag.get("href")
            attrs = {}
            if href:
                attrs["href"] = href
            tag.attrs = attrs
        else:
            tag.attrs = {}

    return str(soup)


def extract_photo_url(style_value: Optional[str]) -> Optional[str]:
    if not style_value:
        return None
    match = PHOTO_URL_RE.search(style_value)
    if not match:
        return None
    return match.group(2)


def make_absolute_media_urls(urls: Iterable[str], base_url: str) -> list[str]:
    return [urljoin(base_url, url) for url in urls]


def dumps_media_urls(urls: Iterable[str]) -> str:
    return json.dumps(list(urls), ensure_ascii=True)


def clip_error(message: str, limit: int = 500) -> str:
    text = " ".join(message.split())
    return text[:limit]


def is_missing_post_page(soup: BeautifulSoup) -> bool:
    return soup.select_one(".tgme_widget_message_wrap") is None


def get_text_plain(tag: Optional[Tag]) -> Optional[str]:
    if tag is None:
        return None
    text = tag.get_text("\n", strip=True)
    return text or None


def normalize_text_html(tag: Optional[Tag]) -> Optional[str]:
    if tag is None:
        return None
    cleaned = sanitize_html_fragment(tag.decode_contents())
    if not cleaned.strip():
        return None
    return cleaned
