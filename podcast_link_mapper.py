from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup


LOGGER = logging.getLogger("podcast_link_mapper")
EPISODE_TITLE_RE = re.compile(r"Эпизод\s*(\d+)\s*[\.:]?\s*(.+)?", re.IGNORECASE)
PODCAST_LINK_RE = re.compile(r"^https://t\.me/mavestreambot/app\?startapp=lovebusiness")
GENERIC_ANCHOR_TEXTS = {
    "",
    "тык",
    "[тык]",
    "[",
    "]",
    "🎙",
}


@dataclass(slots=True)
class TrackRecord:
    playlist_index: int
    episode_number: int
    album_id: str
    track_id: str
    track_url: str
    title: str
    normalized_title: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build and optionally apply Yandex Music replacements for Telegram podcast links.",
    )
    parser.add_argument("--db", required=True, help="SQLite database path.")
    parser.add_argument("--tracks-json", required=True, help="JSON file with collected Yandex tracks.")
    parser.add_argument(
        "--report-json",
        default="podcast_link_mapping_report.json",
        help="Where to save the mapping report.",
    )
    parser.add_argument("--apply", action="store_true", help="Apply replacements into text_html/text_plain.")
    parser.add_argument("--backup-db", action="store_true", help="Create a DB backup before applying.")
    return parser.parse_args()


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


def normalize_title(value: str | None) -> str:
    if not value:
        return ""
    text = value.lower()
    text = text.replace("ё", "е")
    text = text.replace("—", " ")
    text = text.replace("–", " ")
    text = text.replace("«", " ")
    text = text.replace("»", " ")
    text = text.replace("…", " ")
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
        r"\b(Этот эпизод|В этом эпизоде|В этом выпуске|Почему|Что|Кто|Можно ли|А что)\b",
        title,
        maxsplit=1,
    )[0].strip()
    return title or None


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


def load_tracks(path: Path) -> dict[int, TrackRecord]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    track_count = len(payload)
    tracks_by_episode: dict[int, TrackRecord] = {}
    for item in payload:
        playlist_index = int(item["playlist_index"])
        episode_number = track_count - playlist_index + 1
        title = str(item["title"]).strip()
        tracks_by_episode[episode_number] = TrackRecord(
            playlist_index=playlist_index,
            episode_number=episode_number,
            album_id=str(item["album_id"]),
            track_id=str(item["track_id"]),
            track_url=str(item["track_url"]).strip(),
            title=title,
            normalized_title=normalize_title(title),
        )
    return tracks_by_episode


def infer_current_episode(soup: BeautifulSoup, text_plain: str | None) -> tuple[int | None, str | None]:
    explicit_candidates: list[tuple[int, str | None]] = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        if not PODCAST_LINK_RE.match(href):
            continue
        episode_number, title = extract_episode_number_and_title(anchor.get_text(" ", strip=True))
        if episode_number is None:
            continue
        explicit_candidates.append((episode_number, title))

    unique_numbers = {item[0] for item in explicit_candidates}
    if len(unique_numbers) == 1 and explicit_candidates:
        episode_number = explicit_candidates[0][0]
        titles = [title for _, title in explicit_candidates if title]
        return episode_number, titles[0] if titles else None

    return extract_episode_number_and_title(text_plain)


def infer_target_episode(
    anchor_text: str,
    current_episode_number: int | None,
) -> tuple[int | None, str]:
    normalized_anchor = normalize_title(anchor_text)
    explicit_episode_number, _ = extract_episode_number_and_title(anchor_text)
    if explicit_episode_number is not None:
        return explicit_episode_number, "anchor_episode_number"
    if "первого эпизода" in normalized_anchor or "с первого эпизода" in normalized_anchor:
        return 1, "first_episode_phrase"
    if normalized_anchor in GENERIC_ANCHOR_TEXTS or re.fullmatch(r"[\[\]\s🎙]+", anchor_text):
        return current_episode_number, "current_post_episode"
    return current_episode_number, "current_post_fallback"


def build_occurrence_report(
    connection: sqlite3.Connection,
    tracks_by_episode: dict[int, TrackRecord],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = connection.execute(
        """
        SELECT id, telegram_post_id, text_html, text_plain
        FROM posts
        WHERE text_html LIKE '%https://t.me/mavestreambot/app?startapp=lovebusiness%'
        ORDER BY telegram_post_id ASC
        """
    ).fetchall()

    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for row in rows:
        text_html = row["text_html"] or ""
        soup = BeautifulSoup(text_html, "html.parser")
        current_episode_number, current_episode_title = infer_current_episode(soup, row["text_plain"])

        link_index = 0
        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href") or "").strip()
            if not PODCAST_LINK_RE.match(href):
                continue

            link_index += 1
            anchor_text = anchor.get_text(" ", strip=True)
            target_episode_number, source = infer_target_episode(anchor_text, current_episode_number)

            entry = {
                "post_id": int(row["telegram_post_id"]),
                "db_row_id": int(row["id"]),
                "link_index": link_index,
                "raw_link": href,
                "anchor_text": anchor_text,
                "current_episode_number": current_episode_number,
                "current_episode_title": current_episode_title,
                "target_episode_number": target_episode_number,
                "resolution_source": source,
            }

            if target_episode_number is None:
                entry["reason"] = "missing_target_episode_number"
                unresolved.append(entry)
                continue

            track = tracks_by_episode.get(target_episode_number)
            if track is None:
                entry["reason"] = "episode_not_found_in_playlist"
                unresolved.append(entry)
                continue

            explicit_number, explicit_title = extract_episode_number_and_title(anchor_text)
            if explicit_number is not None and explicit_title:
                if normalize_title(explicit_title) != track.normalized_title:
                    entry["reason"] = "explicit_title_mismatch"
                    entry["expected_track_title"] = track.title
                    unresolved.append(entry)
                    continue

            entry["track_title"] = track.title
            entry["track_url"] = track.track_url
            entry["track_id"] = track.track_id
            entry["playlist_index"] = track.playlist_index
            resolved.append(entry)

    return resolved, unresolved


def write_report(report_path: Path, resolved: list[dict[str, Any]], unresolved: list[dict[str, Any]]) -> None:
    payload = {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "resolved_count": len(resolved),
        "unresolved_count": len(unresolved),
        "resolved": resolved,
        "unresolved": unresolved,
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def backup_database(db_path: Path) -> Path:
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.with_name(f"posts_backup_before_podcast_links_{timestamp}.db")
    shutil.copy2(db_path, backup_path)
    return backup_path


def apply_replacements(connection: sqlite3.Connection, resolved: list[dict[str, Any]]) -> dict[str, int]:
    resolved_by_post: dict[int, list[dict[str, Any]]] = {}
    for item in resolved:
        resolved_by_post.setdefault(int(item["db_row_id"]), []).append(item)

    updated_posts = 0
    html_replacements = 0
    plain_replacements = 0

    for row_id, replacements in resolved_by_post.items():
        row = connection.execute(
            "SELECT text_html, text_plain FROM posts WHERE id = ?",
            (row_id,),
        ).fetchone()
        if row is None:
            continue

        text_html = row["text_html"] or ""
        text_plain = row["text_plain"] or ""
        new_html = text_html
        new_plain = text_plain

        for replacement in replacements:
            raw_link = str(replacement["raw_link"])
            target_link = str(replacement["track_url"])
            if raw_link in new_html:
                new_html = new_html.replace(raw_link, target_link)
                html_replacements += 1
            if raw_link in new_plain:
                new_plain = new_plain.replace(raw_link, target_link)
                plain_replacements += 1

        if new_html != text_html or new_plain != text_plain:
            connection.execute(
                "UPDATE posts SET text_html = ?, text_plain = ? WHERE id = ?",
                (new_html, new_plain, row_id),
            )
            updated_posts += 1

    connection.commit()
    return {
        "updated_posts": updated_posts,
        "html_replacements": html_replacements,
        "plain_replacements": plain_replacements,
    }


def main() -> int:
    args = parse_args()
    configure_logging()

    db_path = Path(args.db)
    tracks_path = Path(args.tracks_json)
    report_path = Path(args.report_json)

    tracks_by_episode = load_tracks(tracks_path)

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        resolved, unresolved = build_occurrence_report(connection, tracks_by_episode)
        write_report(report_path, resolved, unresolved)

        LOGGER.info("Resolved podcast links: %s", len(resolved))
        LOGGER.info("Unresolved podcast links: %s", len(unresolved))
        LOGGER.info("Saved report: %s", report_path)

        if not args.apply:
            return 0

        if args.backup_db:
            backup_path = backup_database(db_path)
            LOGGER.info("Created DB backup: %s", backup_path)

        stats = apply_replacements(connection, resolved)
        LOGGER.info("Updated posts: %s", stats["updated_posts"])
        LOGGER.info("HTML replacements: %s", stats["html_replacements"])
        LOGGER.info("Plain replacements: %s", stats["plain_replacements"])
    finally:
        connection.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
