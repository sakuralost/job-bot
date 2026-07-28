from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.getenv("JOB_BOT_DB_PATH", "/app/data/job-bot.sqlite3"))


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sources (
              id INTEGER PRIMARY KEY, handle TEXT NOT NULL UNIQUE,
              label TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
              last_processed_message_id INTEGER NOT NULL DEFAULT 0,
              last_success_at TEXT, last_error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS messages (
              id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id),
              telegram_message_id INTEGER NOT NULL, sent_at TEXT, text TEXT NOT NULL,
              sender_username TEXT, sender_id TEXT, message_url TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(source_id, telegram_message_id)
            );
            CREATE TABLE IF NOT EXISTS analyses (
              id INTEGER PRIMARY KEY, message_id INTEGER NOT NULL UNIQUE REFERENCES messages(id),
              is_match INTEGER NOT NULL, confidence REAL NOT NULL, reason TEXT NOT NULL,
              model_name TEXT, prompt_version TEXT, result_json TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'new', note TEXT, analyzed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS runs (
              id INTEGER PRIMARY KEY, started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              finished_at TEXT, status TEXT NOT NULL, processed_count INTEGER NOT NULL DEFAULT 0,
              matched_count INTEGER NOT NULL DEFAULT 0, detail TEXT
            );
            CREATE TABLE IF NOT EXISTS preferences (
              id INTEGER PRIMARY KEY CHECK(id = 1), payload_json TEXT NOT NULL DEFAULT '{}',
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT OR IGNORE INTO preferences(id, payload_json) VALUES(1, '{}');
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(sources)")}
        if "parent_source_id" not in columns:
            conn.execute("ALTER TABLE sources ADD COLUMN parent_source_id INTEGER REFERENCES sources(id)")
        if "is_auto_discovered" not in columns:
            conn.execute("ALTER TABLE sources ADD COLUMN is_auto_discovered INTEGER NOT NULL DEFAULT 0")
        message_columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
        if "original_text" not in message_columns:
            conn.execute("ALTER TABLE messages ADD COLUMN original_text TEXT")
            conn.execute("UPDATE messages SET original_text=text WHERE original_text IS NULL")
        if "fingerprint" not in message_columns:
            conn.execute("ALTER TABLE messages ADD COLUMN fingerprint TEXT")
        if "duplicate_of_message_id" not in message_columns:
            conn.execute("ALTER TABLE messages ADD COLUMN duplicate_of_message_id INTEGER REFERENCES messages(id)")


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def preferences() -> dict:
    with connect() as conn:
        return json.loads(conn.execute("SELECT payload_json FROM preferences WHERE id=1").fetchone()[0])


def save_preferences(payload: dict) -> None:
    with connect() as conn:
        conn.execute("UPDATE preferences SET payload_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=1", (json.dumps(payload, ensure_ascii=False),))
