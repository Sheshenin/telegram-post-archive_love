from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional


@dataclass(slots=True)
class PostRecord:
    telegram_post_id: int
    url: str
    published_at: Optional[str]
    text_html: Optional[str]
    text_plain: Optional[str]
    has_media: bool
    media_type: str
    media_urls: str
    raw_html: Optional[str]
    status: str
    error: Optional[str]


@dataclass(slots=True)
class MaxPublishPost:
    id: int
    telegram_post_id: int
    url: str
    text_html: Optional[str]
    media_type: str
    media_urls: list[str]
    max_message_id: Optional[str]
    max_message_url: Optional[str]

    @classmethod
    def from_row(cls, row) -> "MaxPublishPost":
        raw_media_urls = row["media_urls"] or "[]"
        try:
            media_urls = json.loads(raw_media_urls)
        except json.JSONDecodeError:
            media_urls = []

        return cls(
            id=int(row["id"]),
            telegram_post_id=int(row["telegram_post_id"]),
            url=str(row["url"]),
            text_html=row["text_html"],
            media_type=str(row["media_type"] or "none"),
            media_urls=[str(url) for url in media_urls if isinstance(url, str)],
            max_message_id=row["max_message_id"],
            max_message_url=row["max_message_url"],
        )
