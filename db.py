"""
SQLite database setup for reminders.
"""
from __future__ import annotations

import sqlite3
import os

DB_PATH = os.environ.get("DB_PATH", "reminders.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id           INTEGER NOT NULL,
            task              TEXT NOT NULL,
            deadline          TEXT,
            interval_minutes  INTEGER DEFAULT 60,
            next_nudge        TEXT,
            done              INTEGER DEFAULT 0,
            created_at        TEXT DEFAULT (datetime('now')),
            nudge_count       INTEGER DEFAULT 0,
            is_recurring      INTEGER DEFAULT 0,
            cron_expression   TEXT,
            cron_every_other  INTEGER DEFAULT 0,
            last_cron_fire    TEXT,
            priority          INTEGER DEFAULT 2,
            snooze_count      INTEGER DEFAULT 0
        )
    """)
    conn.commit()

    # Auto-migrate existing DBs
    existing = [row[1] for row in conn.execute("PRAGMA table_info(reminders)").fetchall()]
    for col, definition in [
        ("cron_expression",  "TEXT"),
        ("cron_every_other", "INTEGER DEFAULT 0"),
        ("last_cron_fire",   "TEXT"),
        ("priority",         "INTEGER DEFAULT 2"),
        ("snooze_count",     "INTEGER DEFAULT 0"),
    ]:
        if col not in existing:
            conn.execute(f"ALTER TABLE reminders ADD COLUMN {col} {definition}")
    conn.commit()
    conn.close()