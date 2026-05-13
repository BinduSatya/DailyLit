# ---------------------------
# Database
# ---------------------------
from dotenv import load_dotenv
import os
import sqlite3
from pathlib import Path
from contextlib import closing
from typing import Any, Optional
import json

load_dotenv()

DB_PATH = Path(os.getenv("DB_PATH", "daily_litting_bot.db"))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(_connect()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                subscribed INTEGER NOT NULL DEFAULT 0,
                current_slug TEXT,
                hint_level INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS potd_cache (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                slug TEXT,
                title TEXT,
                difficulty TEXT,
                content TEXT,
                topics_json TEXT,
                url TEXT,
                fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()

def upsert_user(telegram_id: int, username: str | None, first_name: str | None) -> None:
    with closing(_connect()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO users (telegram_id, username, first_name)
            VALUES (?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                updated_at = CURRENT_TIMESTAMP
            """,
            (telegram_id, username, first_name),
        )
        conn.commit()


def set_subscribed(telegram_id: int, value: bool) -> None:
    with closing(_connect()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE users
            SET subscribed = ?, updated_at = CURRENT_TIMESTAMP
            WHERE telegram_id = ?
            """,
            (1 if value else 0, telegram_id),
        )
        conn.commit()


def reset_hint_state_for_new_slug(telegram_id: int, slug: str) -> None:
    with closing(_connect()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE users
            SET current_slug = ?, hint_level = 0, updated_at = CURRENT_TIMESTAMP
            WHERE telegram_id = ? AND COALESCE(current_slug, '') <> ?
            """,
            (slug, telegram_id, slug),
        )
        conn.commit()

def get_user(telegram_id: int) -> Optional[sqlite3.Row]:
    with closing(_connect()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        return cur.fetchone()


def get_subscribed_users() -> list[sqlite3.Row]:
    with closing(_connect()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE subscribed = 1")
        return cur.fetchall()


def get_hint_level(telegram_id: int) -> int:
    user = get_user(telegram_id)
    return int(user["hint_level"]) if user else 0


def set_hint_level(telegram_id: int, level: int, slug: str) -> None:
    with closing(_connect()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE users
            SET hint_level = ?, current_slug = ?, updated_at = CURRENT_TIMESTAMP
            WHERE telegram_id = ?
            """,
            (level, slug, telegram_id),
        )
        conn.commit()


def cache_potd(potd: dict[str, Any]) -> None:
    with closing(_connect()) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO potd_cache (id, slug, title, difficulty, content, topics_json, url)
            VALUES (1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                slug = excluded.slug,
                title = excluded.title,
                difficulty = excluded.difficulty,
                content = excluded.content,
                topics_json = excluded.topics_json,
                url = excluded.url,
                fetched_at = CURRENT_TIMESTAMP
            """,
            (
                potd["slug"],
                potd["title"],
                potd["difficulty"],
                potd["content"],
                json.dumps(potd["topics"], ensure_ascii=False),
                potd["url"],
            ),
        )
        conn.commit()

def load_cached_potd() -> Optional[dict[str, Any]]:
    with closing(_connect()) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM potd_cache WHERE id = 1")
        row = cur.fetchone()
        if not row:
            return None
        return {
            "slug": row["slug"],
            "title": row["title"],
            "difficulty": row["difficulty"],
            "content": row["content"],
            "topics": json.loads(row["topics_json"] or "[]"),
            "url": row["url"],
        }

