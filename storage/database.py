"""
SQLite 聊天记录
"""
import os
import sqlite3
import threading
import time
from typing import Optional

from core.config import DB_PATH


class Database:
    def __init__(self, path: str = DB_PATH):
        self.path = path
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self._init_schema()

    def _init_schema(self):
        with self._lock:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    time REAL NOT NULL,
                    sender TEXT,
                    content TEXT,
                    msg_id TEXT UNIQUE,
                    type TEXT
                )
            """)
            # 清理 180 天前的记录
            cutoff = time.time() - 180 * 24 * 3600
            self.conn.execute("DELETE FROM messages WHERE time < ?", (cutoff,))
            self.conn.commit()

    def save_message(self, time_val: float, sender: str, content: str,
                     msg_id: str, msg_type: str = "chat") -> bool:
        """返回 True 表示新插入，False 表示重复"""
        with self._lock:
            try:
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO messages (time, sender, content, msg_id, type) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (time_val, sender, content, msg_id, msg_type),
                )
                self.conn.commit()
                return cur.rowcount > 0
            except Exception as e:
                print(f"[DB] 保存失败: {e}")
                return False

    def get_recent(self, limit: int = 20) -> list:
        with self._lock:
            cur = self.conn.execute(
                "SELECT time, sender, content, type FROM messages "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            return list(reversed(cur.fetchall()))

    def get_since_id(self, last_id: int, limit: int = 50, offset: int = 0) -> list:
        with self._lock:
            cur = self.conn.execute(
                "SELECT id, time, sender, content, msg_id FROM messages "
                "WHERE id > ? AND type='chat' ORDER BY id ASC LIMIT ? OFFSET ?",
                (last_id, limit, offset),
            )
            return list(cur.fetchall())

    def max_msg_id(self) -> int:
        with self._lock:
            cur = self.conn.execute("SELECT COALESCE(MAX(id), 0) FROM messages")
            return cur.fetchone()[0]

    def close(self):
        with self._lock:
            try:
                self.conn.close()
            except Exception:
                pass