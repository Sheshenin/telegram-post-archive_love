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
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup


LOGGER = logging.getLogger("podcast_link_mapper")
EPISODE_TITLE_RE = re.compile(r"Эпизод\s*(\d+)\s*[\.:]\s*(.+)", re.IGNORECASE)
STARTAPP_RE = re.compile(r"^lovebusiness(?:_(\d+))?(?:_player)?$")


@dataclass(slots=True)
class TrackRecord:
    track_id: str
    track_url: str
    title: str
    normalized_title: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build and optionally apply safe Yandex Music replacements for Telegram podcast links.",
    )
    parser.add_argument("--db", required=True, help="SQLite database path.")
    parser.add_argument("--tracks-json", required=True, help="JSON file from yandex_album_scraper.py.")
    parser.add_argument(
        "--report-json",
        default="podcast_link_mapping_report.json",
        help="Where to save the mapping report.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply confirmed replacements into text_html/text_plain.",
    )
    parser.add_argument(
        "--backup-db",
        action="store_true",
        help="Create a timestamped DB backup before applying replacements.",
    )
    return parser.parse_args()


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


def normalize_title(value: str) -> str:
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


def load_tracks(path: Path) -> tuple[dict[str, TrackRecord], dict[str, list[TrackRecord]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tracks_by_title: dict[str, list[TrackRecord]] = {}
    tracks_by_id: dict[str, TrackRecord] = {}
    for item in payload:
        title = str(item["title"]).strip()
        track = TrackRecord(
            track_id=str(item["track_id"]),
            track_url=str(item["track_url"]).strip(),
            title=title,
            normalized_title=normalize_title(title),
        )
        tracks_by_id[track.track_id] = track
        tracks_by_title.setdefault(track.normalized_title, []).append(track)
    return tracks_by_id, tracks_by_title


def extract_episode_title(text: str | None) -> tuple[int | None, str | None]:
    if not text:
        return None, None
    compact = re.sub(r"\s+", " ", text).strip()
    match = EPISODE_TITLE_RE.search(compact)
    if not match:
        return None, None
    episode_number = int(match.group(1))
    title = match.group(2).strip()
    title = re.split(r"\s{2,}| В этом эпизоде| Этот эпизод| Почему | Что | Кто ", title, maxsplit=1)[0]
    return episode_number, title.strip()


def get_startapp_key(href: str) -> str | None:
    parsed = urlparse(href)
    query = parse_qs(parsed.query)
    raw_startapp = query.get("startapp", [None])[0]
    if raw_startapp is None:
        return None
    match = STARTAPP_RE.match(raw_startapp)
    if not match:
        return None
    if raw_startapp.endswith("_player"):
        return raw_startapp[: -len("_player")]
    return raw_startapp


def build_candidate_map(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, telegram_post_id, text_html, text_plain
        FROM posts
        WHERE text_html LIKE '%t.me/mavestreambot/app?startapp=lovebusiness%'
        ORDER BY telegram_post_id ASC
        """
    ).fetchall()

    candidate_map: dict[str, dict[str, Any]] = {}
    for row in rows:
        soup = BeautifulSoup(row["text_html"] or "", "html.parser")
        explicit_titles: dict[str, tuple[int | None, str]] = {}
        for anchor in soup.find_all("a", href=True):
            href = str(anchor["href"]).strip()
            if "t.me/mavestreambot/app?startapp=lovebusiness" not in href:
                continue
            anchor_text = anchor.get_text(" ", strip=True)
            link_episode_number, link_title = extract_episode_title(anchor_text)
            if not link_title:
                continue
            explicit_titles.setdefault(normalize_title(link_title), (link_episode_number, link_title))

        if len(explicit_titles) == 1:
            current_episode_number, current_episode_title = next(iter(explicit_titles.values()))
        else:
            current_episode_number, current_episode_title = extract_episode_title(row["text_plain"])

        for anchor in soup.find_all("a", href=True):
            href = str(anchor["href"]).strip()
            if "t.me/mavestreambot/app?startapp=lovebusiness" not in href:
                continue

            startapp_key = get_startapp_key(href)
            if startapp_key is None:
                continue

            anchor_text = anchor.get_text(" ", strip=True)
            link_episode_number, link_title = extract_episode_title(anchor_text)

            if link_title:
                episode_number = link_episode_number
                candidate_title = link_title
                source = "anchor_episode_title"
            elif current_episode_title and anchor_text.lower() in {"тык", "", "слушать", "слушать эпизод"}:
                episode_number = current_episode_number
                candidate_title = current_episode_title
                source = "current_post_title"
            else:
                episode_number = None
                candidate_title = None
                source = "unresolved_anchor"

            entry = candidate_map.setdefault(
                startapp_key,
                {
                    "candidate_titles": {},
                    "source_posts": set(),
                    "raw_links": set(),
                    "anchor_texts": set(),
                    "episode_numbers": set(),
                    "unresolved_posts": set(),
                },
            )
            entry["source_posts"].add(int(row["telegram_post_id"]))
            entry["raw_links"].add(href)
            if anchor_text:
                entry["anchor_texts"].add(anchor_text)
            if episode_number is not None:
                entry["episode_numbers"].add(int(episode_number))
            if candidate_title:
                normalized = normalize_title(candidate_title)
                bucket = entry["candidate_titles"].setdefault(
                    normalized,
                    {"title": candidate_title, "sources": set(), "source_kinds": set()},
                )
                bucket["sources"].add(int(row["telegram_post_id"]))
                bucket["source_kinds"].add(source)
            else:
                entry["unresolved_posts"].add(int(row["telegram_post_id"]))
    return candidate_map


def build_resolutions(
    candidate_map: dict[str, dict[str, Any]],
    tracks_by_title: dict[str, list[TrackRecord]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    resolved: dict[str, dict[str, Any]] = {}
    unresolved: list[dict[str, Any]] = []

    for startapp_key, entry in sorted(candidate_map.items()):
        candidate_titles = entry["candidate_titles"]
        if len(candidate_titles) != 1 or entry["unresolved_posts"]:
            unresolved.append(
                {
                    "startapp_key": startapp_key,
                    "reason": (
                        "contains_unresolved_occurrences"
                        if entry["unresolved_posts"]
                        else "ambiguous_or_missing_candidate_title"
                    ),
                    "candidate_titles": [
                        {
                            "title": item["title"],
                            "sources": sorted(item["sources"]),
                            "source_kinds": sorted(item["source_kinds"]),
                        }
                        for item in candidate_titles.values()
                    ],
                    "source_posts": sorted(entry["source_posts"]),
                    "unresolved_posts": sorted(entry["unresolved_posts"]),
                    "raw_links": sorted(entry["raw_links"]),
                    "anchor_texts": sorted(entry["anchor_texts"]),
                    "episode_numbers": sorted(entry["episode_numbers"]),
                }
            )
            continue

        normalized_title, item = next(iter(candidate_titles.items()))
        tracks = tracks_by_title.get(normalized_title, [])
        if len(tracks) != 1:
            unresolved.append(
                {
                    "startapp_key": startapp_key,
                    "reason": "track_title_not_unique_or_missing",
                    "candidate_title": item["title"],
                    "matching_tracks": [
                        {"track_id": track.track_id, "track_url": track.track_url, "title": track.title}
                        for track in tracks
                    ],
                    "source_posts": sorted(entry["source_posts"]),
                    "unresolved_posts": sorted(entry["unresolved_posts"]),
                    "raw_links": sorted(entry["raw_links"]),
                    "anchor_texts": sorted(entry["anchor_texts"]),
                    "episode_numbers": sorted(entry["episode_numbers"]),
                }
            )
            continue

        track = tracks[0]
        resolved[startapp_key] = {
            "startapp_key": startapp_key,
            "candidate_title": item["title"],
            "normalized_title": normalized_title,
            "track_id": track.track_id,
            "track_url": track.track_url,
            "track_title": track.title,
            "source_posts": sorted(entry["source_posts"]),
            "raw_links": sorted(entry["raw_links"]),
            "anchor_texts": sorted(entry["anchor_texts"]),
            "episode_numbers": sorted(entry["episode_numbers"]),
        }
    return resolved, unresolved


def write_report(
    report_path: Path,
    resolved: dict[str, dict[str, Any]],
    unresolved: list[dict[str, Any]],
) -> None:
    payload = {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "resolved_count": len(resolved),
        "unresolved_count": len(unresolved),
        "resolved": [resolved[key] for key in sorted(resolved)],
        "unresolved": unresolved,
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def backup_database(db_path: Path) -> Path:
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.with_name(f"posts_backup_before_podcast_links_{timestamp}.db")
    shutil.copy2(db_path, backup_path)
    return backup_path


def apply_replacements(
    connection: sqlite3.Connection,
    resolved: dict[str, dict[str, Any]],
) -> dict[str, int]:
    rows = connection.execute(
        """
        SELECT id, text_html, text_plain
        FROM posts
        WHERE text_html LIKE '%t.me/mavestreambot/app?startapp=lovebusiness%'
        """
    ).fetchall()

    updated_posts = 0
    html_replacements = 0
    plain_replacements = 0

    for row in rows:
        text_html = row["text_html"] or ""
        text_plain = row["text_plain"] or ""
        new_html = text_html
        new_plain = text_plain

        for info in resolved.values():
            for raw_link in info["raw_links"]:
                if raw_link in new_html:
                    new_html = new_html.replace(raw_link, info["track_url"])
                    html_replacements += 1
                if raw_link in new_plain:
                    new_plain = new_plain.replace(raw_link, info["track_url"])
                    plain_replacements += 1

        if new_html != text_html or new_plain != text_plain:
            connection.execute(
                "UPDATE posts SET text_html = ?, text_plain = ? WHERE id = ?",
                (new_html, new_plain, int(row["id"])),
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

    _, tracks_by_title = load_tracks(tracks_path)

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        candidate_map = build_candidate_map(connection)
        resolved, unresolved = build_resolutions(candidate_map, tracks_by_title)
        write_report(report_path, resolved, unresolved)

        LOGGER.info("Resolved podcast link keys: %s", len(resolved))
        LOGGER.info("Unresolved podcast link keys: %s", len(unresolved))
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
