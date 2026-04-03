from __future__ import annotations

import sqlite3
from pathlib import Path

from models import MaxPublishPost, PostRecord


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_post_id INTEGER UNIQUE,
    url TEXT,
    published_at DATETIME,
    text_html TEXT,
    text_plain TEXT,
    has_media BOOLEAN,
    media_type TEXT,
    media_urls TEXT,
    raw_html TEXT,
    status TEXT,
    error TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);
CREATE INDEX IF NOT EXISTS idx_posts_post_id ON posts(telegram_post_id);
"""

PUBLISH_COLUMNS = (
    ("published_to_max", "BOOLEAN DEFAULT 0"),
    ("max_message_id", "TEXT"),
    ("max_message_url", "TEXT"),
    ("published_at_max", "DATETIME"),
)


def connect_db(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    if path.parent and path.parent != Path("."):
        path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA_SQL)
    ensure_publish_columns(connection)
    return connection


def ensure_publish_columns(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(posts)").fetchall()
    }

    for column_name, column_def in PUBLISH_COLUMNS:
        if column_name in columns:
            continue
        connection.execute(f"ALTER TABLE posts ADD COLUMN {column_name} {column_def}")

    connection.commit()


def post_exists(connection: sqlite3.Connection, telegram_post_id: int) -> bool:
    row = connection.execute(
        "SELECT 1 FROM posts WHERE telegram_post_id = ?",
        (telegram_post_id,),
    ).fetchone()
    return row is not None


def get_post_status(connection: sqlite3.Connection, telegram_post_id: int) -> str | None:
    row = connection.execute(
        "SELECT status FROM posts WHERE telegram_post_id = ?",
        (telegram_post_id,),
    ).fetchone()
    if row is None:
        return None
    return str(row["status"])


def insert_post(connection: sqlite3.Connection, post: PostRecord) -> None:
    connection.execute(
        """
        INSERT INTO posts (
            telegram_post_id,
            url,
            published_at,
            text_html,
            text_plain,
            has_media,
            media_type,
            media_urls,
            raw_html,
            status,
            error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            post.telegram_post_id,
            post.url,
            post.published_at,
            post.text_html,
            post.text_plain,
            int(post.has_media),
            post.media_type,
            post.media_urls,
            post.raw_html,
            post.status,
            post.error,
        ),
    )
    connection.commit()


def update_post_content(connection: sqlite3.Connection, post: PostRecord) -> None:
    connection.execute(
        """
        UPDATE posts
        SET url = ?,
            published_at = ?,
            text_html = ?,
            text_plain = ?,
            has_media = ?,
            media_type = ?,
            media_urls = ?,
            raw_html = ?,
            status = ?,
            error = ?
        WHERE telegram_post_id = ?
        """,
        (
            post.url,
            post.published_at,
            post.text_html,
            post.text_plain,
            int(post.has_media),
            post.media_type,
            post.media_urls,
            post.raw_html,
            post.status,
            post.error,
            post.telegram_post_id,
        ),
    )
    connection.commit()


def get_posts_for_max(
    connection: sqlite3.Connection,
    limit: int,
    single_post_id: int | None = None,
) -> list[MaxPublishPost]:
    params: list[object] = []
    query = """
        SELECT id, telegram_post_id, text_html, media_type, media_urls
             , url, max_message_id, max_message_url
        FROM posts
        WHERE status = 'success'
          AND COALESCE(published_to_max, 0) = 0
    """

    if single_post_id is not None:
        query += " AND telegram_post_id = ?"
        params.append(single_post_id)

    query += " ORDER BY telegram_post_id ASC LIMIT ?"
    params.append(limit)

    rows = connection.execute(query, params).fetchall()
    return [MaxPublishPost.from_row(row) for row in rows]


def mark_post_published(
    connection: sqlite3.Connection,
    row_id: int,
    max_message_id: str,
    max_message_url: str | None,
) -> None:
    connection.execute(
        """
        UPDATE posts
        SET published_to_max = 1,
            max_message_id = ?,
            max_message_url = ?,
            published_at_max = CURRENT_TIMESTAMP,
            error = NULL
        WHERE id = ?
        """,
        (max_message_id, max_message_url, row_id),
    )
    connection.commit()


def mark_post_error(connection: sqlite3.Connection, row_id: int, error: str) -> None:
    connection.execute(
        "UPDATE posts SET error = ? WHERE id = ?",
        (error, row_id),
    )
    connection.commit()


def mark_post_skipped_max(connection: sqlite3.Connection, row_id: int, reason: str) -> None:
    connection.execute(
        """
        UPDATE posts
        SET published_to_max = 1,
            max_message_id = 'SKIPPED_EMPTY',
            max_message_url = NULL,
            published_at_max = CURRENT_TIMESTAMP,
            error = ?
        WHERE id = ?
        """,
        (reason, row_id),
    )
    connection.commit()


def get_max_link_map(connection: sqlite3.Connection) -> dict[int, str]:
    rows = connection.execute(
        """
        SELECT telegram_post_id, max_message_url
        FROM posts
        WHERE COALESCE(published_to_max, 0) = 1
          AND max_message_url IS NOT NULL
        """
    ).fetchall()
    return {int(row["telegram_post_id"]): str(row["max_message_url"]) for row in rows}


def get_published_posts_for_url_sync(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT id, telegram_post_id, max_message_id, max_message_url
        FROM posts
        WHERE COALESCE(published_to_max, 0) = 1
          AND max_message_id IS NOT NULL
        ORDER BY telegram_post_id ASC
        """
    ).fetchall()


def update_max_message_url(connection: sqlite3.Connection, row_id: int, max_message_url: str) -> None:
    connection.execute(
        "UPDATE posts SET max_message_url = ? WHERE id = ?",
        (max_message_url, row_id),
    )
    connection.commit()
