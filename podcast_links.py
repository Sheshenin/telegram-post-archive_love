from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup, NavigableString, Tag

from models import PostRecord


LOGGER = logging.getLogger("podcast_links")
EPISODE_TITLE_RE = re.compile(r"Эпизод\s*(\d+)\s*[\.:]?\s*(.+)?", re.IGNORECASE)
PODCAST_LINK_RE = re.compile(r"^https://t\.me/mavestreambot/app\?startapp=lovebusiness", re.IGNORECASE)
GENERIC_ANCHOR_TEXTS = {"", "тык", "[тык]", "[", "]", "🎙"}
Y_MUSIC_QUERY_ALBUM_HINT = "дело в любви"
Y_MUSIC_RESULT_RE = re.compile(
    r'"type":"podcast_episode","data":\{'
    r'"id":"(?P<track_id>\d+)".*?'
    r'"title":"(?P<title>(?:\\.|[^"])*)".*?'
    r'"albumId":(?P<album_id>\d+).*?'
    r'"albums":\[\{"id":(?P<album_id_2>\d+),"title":"(?P<album_title>(?:\\.|[^"])*)"',
    re.S,
)


def normalize_title(value: str | None) -> str:
    if not value:
        return ""
    text = value.lower()
    text = text.replace("ё", "е").replace("—", " ").replace("–", " ")
    text = text.replace("«", " ").replace("»", " ").replace("…", " ")
    text = re.sub(r"[\"'`.,:;!?()\[\]{}]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_title_tail(value: str | None) -> str | None:
    if not value:
        return None
    title = re.sub(r"\s+", " ", value).strip()
    if not title:
        return None
    title = re.split(r"\s{2,}", title, maxsplit=1)[0]
    title = re.split(
        r"\b(Этот эпизод|В этом эпизоде|В этом выпуске)\b",
        title,
        maxsplit=1,
    )[0].strip()
    return title or None


def extract_title_from_following_siblings(anchor: Tag) -> str | None:
    chunks: list[str] = []
    for sibling in anchor.next_siblings:
        if isinstance(sibling, Tag) and sibling.name == "br":
            break
        if isinstance(sibling, Tag):
            text = sibling.get_text(" ", strip=True)
        elif isinstance(sibling, NavigableString):
            text = str(sibling).strip()
        else:
            text = ""
        if text:
            chunks.append(text)
    if not chunks:
        return None
    candidate = clean_title_tail(" ".join(chunks))
    if not candidate:
        return None
    candidate = candidate.lstrip(".:;- ").strip()
    return candidate or None


def extract_episode_number_and_title(text: str | None) -> tuple[int | None, str | None]:
    if not text:
        return None, None
    compact = re.sub(r"\s+", " ", text).strip()
    match = EPISODE_TITLE_RE.search(compact)
    if not match:
        return None, None
    episode_number = int(match.group(1))
    title = clean_title_tail(match.group(2))
    return episode_number, title


def extract_episode_announcement(soup: BeautifulSoup) -> tuple[int | None, str | None]:
    explicit_candidates: list[tuple[int, str | None]] = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        if not PODCAST_LINK_RE.match(href):
            continue
        episode_number, title = extract_episode_number_and_title(anchor.get_text(" ", strip=True))
        if episode_number is None:
            continue
        if title is None:
            title = extract_title_from_following_siblings(anchor)
        explicit_candidates.append((episode_number, title))

    unique_numbers = {item[0] for item in explicit_candidates}
    if len(unique_numbers) == 1 and explicit_candidates:
        episode_number = explicit_candidates[0][0]
        titles = [title for _, title in explicit_candidates if title]
        return episode_number, titles[0] if titles else None
    return None, None


def infer_target_episode(anchor_text: str, current_episode_number: int | None) -> int | None:
    normalized_anchor = normalize_title(anchor_text)
    explicit_episode_number, _ = extract_episode_number_and_title(anchor_text)
    if explicit_episode_number is not None:
        return explicit_episode_number
    if "первого эпизода" in normalized_anchor or "с первого эпизода" in normalized_anchor:
        return 1
    if normalized_anchor in GENERIC_ANCHOR_TEXTS or re.fullmatch(r"[\[\]\s🎙]+", anchor_text):
        return current_episode_number
    return current_episode_number


def decode_json_string(value: str) -> str:
    return json.loads(f'"{value}"')


class YandexMusicResolver:
    def __init__(
        self,
        tracks_json_path: str | None,
        *,
        album_id: int = 36214929,
        album_title: str = "Дело в любви",
        ssh_host: str | None = None,
        ssh_user: str = "root",
        ssh_key_path: str | None = None,
    ) -> None:
        self.tracks_json_path = Path(tracks_json_path) if tracks_json_path else None
        self.album_id = album_id
        self.album_title = album_title
        self.ssh_host = ssh_host
        self.ssh_user = ssh_user
        self.ssh_key_path = ssh_key_path
        self.track_by_title = self._load_tracks_cache()

    def _load_tracks_cache(self) -> dict[str, str]:
        if not self.tracks_json_path or not self.tracks_json_path.exists():
            return {}
        payload = json.loads(self.tracks_json_path.read_text(encoding="utf-8"))
        mapping: dict[str, str] = {}
        for item in payload:
            title = str(item["title"]).strip()
            track_url = str(item["track_url"]).strip()
            mapping[normalize_title(title)] = track_url
        return mapping

    def resolve_track_url(self, title: str) -> str | None:
        normalized = normalize_title(title)
        if not normalized:
            return None

        cached = self.track_by_title.get(normalized)
        if cached:
            return cached

        html = self._search_yandex_music(f"{title} {Y_MUSIC_QUERY_ALBUM_HINT}")
        if not html:
            return None
        return self._extract_track_url_from_search(html, normalized)

    def _search_yandex_music(self, query: str) -> str | None:
        local_html = self._search_yandex_music_local(query)
        if local_html and "недоступна в вашем регионе" not in local_html.lower():
            return local_html
        if self.ssh_host:
            return self._search_yandex_music_remote(query)
        return None

    def _search_yandex_music_local(self, query: str) -> str | None:
        url = f"https://music.yandex.ru/search?text={quote(query)}"
        try:
            with httpx.Client(
                headers={"User-Agent": "Mozilla/5.0"},
                follow_redirects=True,
                timeout=20.0,
            ) as client:
                response = client.get(url)
                response.raise_for_status()
                return response.text
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Local Yandex search failed for %r: %s", query, exc)
            return None

    def _search_yandex_music_remote(self, query: str) -> str | None:
        if not self.ssh_host:
            return None
        remote_python = (
            "import urllib.parse, urllib.request;"
            f"q=urllib.parse.quote({query!r});"
            "url=f'https://music.yandex.ru/search?text={q}';"
            "req=urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'});"
            "html=urllib.request.urlopen(req, timeout=20).read().decode('utf-8', errors='replace');"
            "print(html)"
        )
        command = ["ssh"]
        if self.ssh_key_path:
            command.extend(["-i", self.ssh_key_path])
        command.append(f"{self.ssh_user}@{self.ssh_host}")
        command.extend(["python3", "-c", remote_python])

        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=40)
            return result.stdout
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Remote Yandex search failed for %r: %s", query, exc)
            return None

    def _extract_track_url_from_search(self, html: str, normalized_title: str) -> str | None:
        for match in Y_MUSIC_RESULT_RE.finditer(html):
            title = decode_json_string(match.group("title")).strip()
            album_title = decode_json_string(match.group("album_title")).strip()
            album_id = int(match.group("album_id"))
            if album_id != self.album_id:
                continue
            if normalize_title(album_title) != normalize_title(self.album_title):
                continue
            if normalize_title(title) != normalized_title:
                continue
            track_id = match.group("track_id")
            return f"https://music.yandex.ru/album/{album_id}/track/{track_id}"
        return None


class PodcastLinkRewriter:
    def __init__(self, resolver: YandexMusicResolver) -> None:
        self.resolver = resolver

    def rewrite_record(self, record: PostRecord) -> PostRecord:
        if not record.text_html:
            return record

        soup = BeautifulSoup(record.text_html, "html.parser")
        current_episode_number, current_title = extract_episode_announcement(soup)
        changed = False

        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href") or "").strip()
            if not PODCAST_LINK_RE.match(href):
                continue

            target_episode = infer_target_episode(anchor.get_text(" ", strip=True), current_episode_number)
            target_title = None
            explicit_episode_number, explicit_title = extract_episode_number_and_title(anchor.get_text(" ", strip=True))
            if explicit_episode_number is not None and explicit_title:
                target_title = explicit_title
            elif target_episode == current_episode_number:
                target_title = current_title

            if not target_title:
                LOGGER.warning(
                    "Podcast link in post %s left unchanged: no explicit episode announcement found in mavestream anchors",
                    record.telegram_post_id,
                )
                continue

            target_url = self.resolver.resolve_track_url(target_title)
            if not target_url:
                LOGGER.warning(
                    "Podcast link in post %s left unchanged: Yandex track not found for %r",
                    record.telegram_post_id,
                    target_title,
                )
                continue

            anchor["href"] = target_url
            changed = True

        if not changed:
            return record

        new_html = soup.decode_contents()
        new_plain = record.text_plain
        return replace(record, text_html=new_html, text_plain=new_plain)
