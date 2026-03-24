"""
SQLite database setup for reminders.
"""

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
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id     INTEGER NOT NULL,
            task        TEXT NOT NULL,
            deadline    TEXT,
            interval_minutes INTEGER DEFAULT 60,
            next_nudge  TEXT,
            done        INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT (datetime('now')),
            nudge_count INTEGER DEFAULT 0,
            is_recurring INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()
