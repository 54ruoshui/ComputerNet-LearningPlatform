"""
学生知识点掌握追踪器
使用 SQLite 存储每个学生对每个实体的掌握状态。
"""

import sqlite3
import threading
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class MasteryTracker:
    def __init__(self, db_path: str = "mastery.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_tables()

    def _init_tables(self):
        with self._lock:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS mastery (
                    student_id  TEXT NOT NULL,
                    entity_name TEXT NOT NULL,
                    mastered    INTEGER NOT NULL DEFAULT 0,
                    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (student_id, entity_name)
                )
            """)
            self.conn.commit()

    def set_mastery(self, student_id: str, entity_name: str, mastered: bool):
        with self._lock:
            self.conn.execute(
                """INSERT INTO mastery (student_id, entity_name, mastered, updated_at)
                   VALUES (?, ?, ?, datetime('now'))
                   ON CONFLICT(student_id, entity_name)
                   DO UPDATE SET mastered = excluded.mastered, updated_at = datetime('now')""",
                (student_id, entity_name, 1 if mastered else 0),
            )
            self.conn.commit()

    def get_mastery(self, student_id: str) -> Dict[str, bool]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT entity_name, mastered FROM mastery WHERE student_id = ?",
                (student_id,),
            ).fetchall()
        return {row[0]: bool(row[1]) for row in rows}

    def get_mastery_summary(self, student_id: str) -> Dict:
        mastery = self.get_mastery(student_id)
        mastered_names = [k for k, v in mastery.items() if v]
        unmastered_names = [k for k, v in mastery.items() if not v]
        return {
            "total": len(mastery),
            "mastered_count": len(mastered_names),
            "unmastered_count": len(unmastered_names),
            "mastered_names": mastered_names,
            "unmastered_names": unmastered_names,
        }

    def close(self):
        if self.conn:
            self.conn.close()
            logger.info("MasteryTracker 数据库连接已关闭")
