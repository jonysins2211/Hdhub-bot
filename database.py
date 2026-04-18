"""
Database module for managing bot settings and post history
Uses SQLite with per-operation connections for thread safety
"""

import sqlite3
import os
from typing import List, Dict, Optional


class Database:
    def __init__(self, db_path: str = 'bot_data.db'):
        self.db_path = db_path
        self._init_database()

    def _connect(self) -> sqlite3.Connection:
        """Create a fresh connection for this operation (thread-safe)"""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_database(self):
        with self._connect() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    url TEXT UNIQUE NOT NULL,
                    posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_url ON posts(url)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_posted_at ON posts(posted_at DESC)')
            conn.commit()

    def set_setting(self, key: str, value: str):
        with self._connect() as conn:
            conn.execute('''
                INSERT OR REPLACE INTO settings (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            ''', (key, value))
            conn.commit()

    def get_setting(self, key: str) -> Optional[str]:
        with self._connect() as conn:
            result = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
            return result['value'] if result else None

    def add_post(self, title: str, url: str) -> bool:
        try:
            with self._connect() as conn:
                conn.execute('INSERT INTO posts (title, url) VALUES (?, ?)', (title, url))
                conn.commit()
                return True
        except sqlite3.IntegrityError:
            return False

    def is_posted(self, url: str) -> bool:
        with self._connect() as conn:
            return conn.execute('SELECT 1 FROM posts WHERE url = ?', (url,)).fetchone() is not None

    def get_recent_posts(self, limit: int = 10) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute(
                'SELECT title, url, posted_at FROM posts ORDER BY posted_at DESC LIMIT ?', (limit,)
            ).fetchall()
            return [dict(row) for row in rows]

    def get_total_posts(self) -> int:
        with self._connect() as conn:
            return conn.execute('SELECT COUNT(*) as count FROM posts').fetchone()['count']

    def get_posts_count_today(self) -> int:
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) as count FROM posts WHERE DATE(posted_at) = DATE('now')"
            ).fetchone()['count']

    def get_unique_content_count(self) -> int:
        return self.get_total_posts()

    def get_last_post_time(self) -> Optional[str]:
        with self._connect() as conn:
            result = conn.execute(
                'SELECT posted_at FROM posts ORDER BY posted_at DESC LIMIT 1'
            ).fetchone()
            return result['posted_at'] if result else None

    def get_size_mb(self) -> float:
        if os.path.exists(self.db_path):
            return os.path.getsize(self.db_path) / (1024 * 1024)
        return 0.0

    def update_post_timestamp(self, url: str):
        with self._connect() as conn:
            conn.execute(
                'UPDATE posts SET updated_at = CURRENT_TIMESTAMP WHERE url = ?', (url,)
            )
            conn.commit()

    def clear_old_posts(self, days: int = 90) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM posts WHERE posted_at < datetime('now', '-' || ? || ' days')", (days,)
            )
            conn.commit()
            return cur.rowcount

    def close(self):
        pass  # Per-operation connections close automatically
