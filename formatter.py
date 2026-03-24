from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup


ALLOWED_TAGS = {"b", "i", "a", "br"}
TAG_NORMALIZATION = {"strong": "b", "em": "i"}
TELEGRAM_HOSTS = {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}
TELEGRAM_POST_RE = re.compile(r"^/(?:(?:s)/)?(?P<channel>[^/]+)/(?P<post_id>\d+)$")
MARUMAUER_RE = re.compile(r"^/(?P<username>@?marumauer)/?$", re.IGNORECASE)


def normalize_telegram_url(href: str) -> str:
    parsed = urlparse(href)
    match = TELEGRAM_POST_RE.match(parsed.path)
    if match:
        return f"https://t.me/{match.group('channel')}/{match.group('post_id')}"
    return f"https://t.me{parsed.path}" if parsed.path else href


def format_text_html(
    text_html: str | None,
    current_channel: str | None,
    link_map: dict[int, str] | None = None,
) -> str:
    if not text_html:
        return ""

    soup = BeautifulSoup(text_html, "html.parser")

    for tag in soup.find_all(True):
        if tag.name in TAG_NORMALIZATION:
            tag.name = TAG_NORMALIZATION[tag.name]

    for br in soup.find_all("br"):
        br.replace_with("\n")

    for link in list(soup.find_all("a", href=True)):
        if is_marumauer_href(str(link.get("href"))):
            replacement_text = sanitize_marumauer_text(link.get_text())
            if replacement_text:
                link.replace_with(replacement_text)
            else:
                link.decompose()

    for tag in list(soup.find_all(True)):
        if tag.name not in ALLOWED_TAGS:
            tag.unwrap()
            continue

        if tag.name == "a":
            href = tag.get("href")
            if href:
                href = rewrite_href(href, current_channel=current_channel, link_map=link_map or {})
            tag.attrs = {"href": href} if href else {}
        else:
            tag.attrs = {}

    rendered = soup.decode_contents().strip()
    return rendered.replace("<br/>", "\n").replace("<br>", "\n")


def rewrite_href(href: str, current_channel: str | None, link_map: dict[int, str]) -> str:
    parsed = urlparse(href)
    host = parsed.netloc.lower()

    if host in TELEGRAM_HOSTS:
        normalized = normalize_telegram_url(href)
        match = TELEGRAM_POST_RE.match(urlparse(normalized).path)
        if match:
            channel = match.group("channel")
            post_id = int(match.group("post_id"))
            if current_channel and channel == current_channel and post_id in link_map:
                mapped = link_map[post_id]
                if "/mid." not in mapped:
                    return mapped
        return normalized

    return href


def is_marumauer_href(href: str) -> bool:
    parsed = urlparse(href)
    if parsed.netloc.lower() not in TELEGRAM_HOSTS:
        return False
    return MARUMAUER_RE.match(parsed.path or "") is not None


def sanitize_marumauer_text(text: str) -> str:
    cleaned = re.sub(r"@?marumauer", "", text, flags=re.IGNORECASE)
    return " ".join(cleaned.split())
