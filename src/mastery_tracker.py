"""
学生知识点掌握追踪器
使用 SQLite 存储每个学生对每个实体的掌握状态。
支持基于艾宾浩斯遗忘曲线的复习调度。
"""

import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import structlog

from src.services.review_scheduler import ReviewScheduler, calculate_retention

logger = structlog.get_logger(__name__)


class MasteryTracker:
    def __init__(self, db_path: str = "data/memory/mastery.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_tables()
        self._scheduler = ReviewScheduler()

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
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS review_state (
                    student_id    TEXT NOT NULL,
                    entity_name   TEXT NOT NULL,
                    review_count  INTEGER NOT NULL DEFAULT 0,
                    last_review_at TEXT,
                    next_review_at TEXT,
                    half_life     REAL NOT NULL DEFAULT 24.0,
                    ease_factor   REAL NOT NULL DEFAULT 2.5,
                    PRIMARY KEY (student_id, entity_name)
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS review_events (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id   TEXT NOT NULL,
                    entity_name  TEXT NOT NULL,
                    event_type   TEXT NOT NULL,
                    quality      INTEGER NOT NULL DEFAULT 0,
                    recorded_at  TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            self.conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_review_events_student
                ON review_events(student_id, recorded_at)
            """)
            self.conn.commit()
            self._init_exam_tables()

    def _init_exam_tables(self):
        # NOTE: called from _init_tables() which already holds self._lock
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS exam_results (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id      TEXT NOT NULL,
                paper_id        TEXT NOT NULL,
                paper_title     TEXT NOT NULL,
                total_score     INTEGER NOT NULL DEFAULT 0,
                max_score       INTEGER NOT NULL DEFAULT 0,
                correct_count   INTEGER NOT NULL DEFAULT 0,
                wrong_count     INTEGER NOT NULL DEFAULT 0,
                completed_at    TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS exam_answer_details (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                result_id       INTEGER NOT NULL,
                question_id     TEXT NOT NULL,
                question_text   TEXT NOT NULL DEFAULT '',
                selected_answer TEXT NOT NULL DEFAULT '',
                correct_answer  TEXT NOT NULL DEFAULT '',
                is_correct      INTEGER NOT NULL DEFAULT 0,
                score           INTEGER NOT NULL DEFAULT 0,
                layer           TEXT NOT NULL DEFAULT '',
                difficulty      TEXT NOT NULL DEFAULT '',
                chapter         TEXT NOT NULL DEFAULT '',
                explanation     TEXT NOT NULL DEFAULT ''
            )
        """)
        # migration: add explanation column if missing
        try:
            self.conn.execute("ALTER TABLE exam_answer_details ADD COLUMN explanation TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS weak_point_profile (
                session_id      TEXT NOT NULL,
                category        TEXT NOT NULL,
                category_value  TEXT NOT NULL,
                error_count     INTEGER NOT NULL DEFAULT 0,
                updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (session_id, category, category_value)
            )
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_exam_results_session
            ON exam_results(session_id, completed_at)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_exam_details_result
            ON exam_answer_details(result_id)
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

    # ==================== 复习调度 ====================

    def record_review(
        self,
        student_id: str,
        entity_name: str,
        quality: int,
        event_type: str = "manual",
    ) -> dict:
        """记录一次复习事件，更新复习状态

        Args:
            student_id: 学生/会话 ID
            entity_name: 知识点实体名
            quality: 回忆质量 0-5
            event_type: 'quiz_correct' | 'quiz_wrong' | 'manual'

        Returns:
            更新后的复习状态
        """
        now = datetime.now()
        with self._lock:
            # 读取现有状态
            row = self.conn.execute(
                """SELECT review_count, half_life, ease_factor
                   FROM review_state WHERE student_id = ? AND entity_name = ?""",
                (student_id, entity_name),
            ).fetchone()

            if row:
                review_count, half_life, ease_factor = row
            else:
                review_count, half_life, ease_factor = 0, 24.0, 2.5

            # 计算新状态
            new_state = self._scheduler.process_review(
                quality=quality,
                review_count=review_count,
                half_life=half_life,
                ease_factor=ease_factor,
                now=now,
            )

            # Upsert review_state
            self.conn.execute(
                """INSERT INTO review_state
                   (student_id, entity_name, review_count, last_review_at,
                    next_review_at, half_life, ease_factor)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(student_id, entity_name)
                   DO UPDATE SET
                       review_count = excluded.review_count,
                       last_review_at = excluded.last_review_at,
                       next_review_at = excluded.next_review_at,
                       half_life = excluded.half_life,
                       ease_factor = excluded.ease_factor""",
                (
                    student_id, entity_name,
                    new_state["review_count"],
                    now.isoformat(),
                    new_state["next_review_at"],
                    new_state["half_life"],
                    new_state["ease_factor"],
                ),
            )

            # 记录事件
            self.conn.execute(
                """INSERT INTO review_events (student_id, entity_name, event_type, quality)
                   VALUES (?, ?, ?, ?)""",
                (student_id, entity_name, event_type, quality),
            )

            self.conn.commit()

        # 构建返回结果
        current = self._scheduler.get_current_state(
            half_life=new_state["half_life"],
            last_review_at=now.isoformat(),
            next_review_at=new_state["next_review_at"],
            review_count=new_state["review_count"],
            now=now,
        )
        return {
            "entity_name": entity_name,
            "review_count": new_state["review_count"],
            "half_life": new_state["half_life"],
            "ease_factor": new_state["ease_factor"],
            "next_review_at": new_state["next_review_at"],
            "next_interval_hours": new_state["next_interval"],
            **current,
        }

    def get_review_state(self, student_id: str) -> List[dict]:
        """获取学生的全部复习状态（含当前保留率等实时计算字段）"""
        now = datetime.now()
        with self._lock:
            rows = self.conn.execute(
                """SELECT entity_name, review_count, last_review_at, next_review_at,
                          half_life, ease_factor
                   FROM review_state WHERE student_id = ?
                   ORDER BY last_review_at DESC""",
                (student_id,),
            ).fetchall()

        result = []
        for row in rows:
            entity_name, review_count, last_at, next_at, half_life, ef = row
            current = self._scheduler.get_current_state(
                half_life=half_life,
                last_review_at=last_at,
                next_review_at=next_at,
                review_count=review_count,
                now=now,
            )
            result.append({
                "entity_name": entity_name,
                "review_count": review_count,
                "last_review_at": last_at,
                "next_review_at": next_at,
                "half_life": round(half_life, 2),
                "ease_factor": round(ef, 2),
                **current,
            })
        return result

    def get_due_reviews(self, student_id: str, lookahead_hours: float = 48) -> List[dict]:
        """获取到期或即将到期的复习项，按紧急度降序排列

        Args:
            student_id: 学生 ID
            lookahead_hours: 向前看多少小时（默认 48 小时内的都算即将到期）
        """
        now = datetime.now()
        cutoff = (now + timedelta(hours=lookahead_hours)).isoformat()
        with self._lock:
            rows = self.conn.execute(
                """SELECT entity_name, review_count, last_review_at, next_review_at,
                          half_life, ease_factor
                   FROM review_state
                   WHERE student_id = ? AND next_review_at <= ?
                   ORDER BY next_review_at ASC""",
                (student_id, cutoff),
            ).fetchall()

        result = []
        total = 0
        for row in rows:
            entity_name, review_count, last_at, next_at, half_life, ef = row
            current = self._scheduler.get_current_state(
                half_life=half_life,
                last_review_at=last_at,
                next_review_at=next_at,
                review_count=review_count,
                now=now,
            )
            result.append({
                "entity_name": entity_name,
                "review_count": review_count,
                "last_review_at": last_at,
                "next_review_at": next_at,
                "half_life": round(half_life, 2),
                "ease_factor": round(ef, 2),
                **current,
            })
            total += 1

        result.sort(key=lambda x: x["urgency"], reverse=True)
        return result

    def get_review_stats(self, student_id: str) -> dict:
        """获取学习效果统计数据"""
        now = datetime.now()
        with self._lock:
            # 复习状态总数
            state_count = self.conn.execute(
                "SELECT COUNT(*) FROM review_state WHERE student_id = ?",
                (student_id,),
            ).fetchone()[0]

            # 复习事件总数
            total_events = self.conn.execute(
                "SELECT COUNT(*) FROM review_events WHERE student_id = ?",
                (student_id,),
            ).fetchone()[0]

            # 今日复习次数
            today = now.strftime("%Y-%m-%d")
            today_events = self.conn.execute(
                "SELECT COUNT(*) FROM review_events WHERE student_id = ? AND recorded_at >= ?",
                (student_id, today),
            ).fetchone()[0]

            # 近 7 天和近 30 天复习次数
            seven_days_ago = (now - timedelta(days=7)).isoformat()
            thirty_days_ago = (now - timedelta(days=30)).isoformat()
            events_7d = self.conn.execute(
                "SELECT COUNT(*) FROM review_events WHERE student_id = ? AND recorded_at >= ?",
                (student_id, seven_days_ago),
            ).fetchone()[0]
            events_30d = self.conn.execute(
                "SELECT COUNT(*) FROM review_events WHERE student_id = ? AND recorded_at >= ?",
                (student_id, thirty_days_ago),
            ).fetchone()[0]

            # 平均保留率（基于实际经过时间）
            rows = self.conn.execute(
                "SELECT half_life, last_review_at FROM review_state WHERE student_id = ?",
                (student_id,),
            ).fetchall()
            if rows:
                total_retention = 0.0
                for hl, last_at in rows:
                    if last_at:
                        last_review = datetime.fromisoformat(last_at)
                        elapsed = (now - last_review).total_seconds() / 3600
                    else:
                        elapsed = 0
                    total_retention += calculate_retention(hl, elapsed)
                avg_retention = total_retention / len(rows)
            else:
                avg_retention = 0

            # 按质量分布
            quality_dist = {}
            for q_row in self.conn.execute(
                "SELECT quality, COUNT(*) as cnt FROM review_events WHERE student_id = ? GROUP BY quality",
                (student_id,),
            ).fetchall():
                quality_dist[str(q_row[0])] = q_row[1]

            # 每日趋势（近 30 天）
            daily_trend = []
            for d_row in self.conn.execute(
                """SELECT date(recorded_at) as day, COUNT(*) as cnt
                   FROM review_events
                   WHERE student_id = ? AND recorded_at >= ?
                   GROUP BY day ORDER BY day""",
                (student_id, thirty_days_ago),
            ).fetchall():
                daily_trend.append({"date": d_row[0], "count": d_row[1]})

            # 待复习数
            due_now = self.conn.execute(
                """SELECT COUNT(*) FROM review_state
                   WHERE student_id = ? AND next_review_at <= ?""",
                (student_id, now.isoformat()),
            ).fetchone()[0]

        return {
            "total_entities": state_count,
            "total_reviews": total_events,
            "today_reviews": today_events,
            "reviews_7d": events_7d,
            "reviews_30d": events_30d,
            "due_now": due_now,
            "avg_retention": round(avg_retention, 4),
            "quality_distribution": quality_dist,
            "daily_trend": daily_trend,
        }

    # ==================== 考试结果追踪 ====================

    def save_exam_result(
        self,
        session_id: str,
        paper_id: str,
        paper_title: str,
        answers: dict,
        graded: dict,
    ) -> int:
        """保存考试结果，返回 result_id"""
        now = datetime.now().isoformat()
        with self._lock:
            cursor = self.conn.execute(
                """INSERT INTO exam_results
                   (session_id, paper_id, paper_title, total_score, max_score,
                    correct_count, wrong_count, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id, paper_id, paper_title,
                    graded["total_score"], graded["max_score"],
                    graded["correct_count"], graded["wrong_count"],
                    now,
                ),
            )
            result_id = cursor.lastrowid

            for detail in graded.get("details", []):
                qid = detail["question_id"]
                user_ans = answers.get(qid, "")
                self.conn.execute(
                    """INSERT INTO exam_answer_details
                       (result_id, question_id, question_text, selected_answer,
                        correct_answer, is_correct, score, layer, difficulty, chapter,
                        explanation)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        result_id, qid, detail.get("text", ""),
                        user_ans, detail["correct_answer"],
                        1 if detail["correct"] else 0, detail.get("score", 0),
                        detail.get("layer", ""), detail.get("difficulty", ""),
                        detail.get("chapter", ""),
                        detail.get("explanation", ""),
                    ),
                )

            self.conn.commit()

        # 更新薄弱点画像
        self.update_weak_point_profile(session_id)
        return result_id

    def get_exam_history(self, session_id: str) -> list:
        with self._lock:
            rows = self.conn.execute(
                """SELECT id, paper_id, paper_title, total_score, max_score,
                          correct_count, wrong_count, completed_at
                   FROM exam_results
                   WHERE session_id = ?
                   ORDER BY completed_at DESC""",
                (session_id,),
            ).fetchall()
        return [
            {
                "result_id": r[0], "paper_id": r[1], "paper_title": r[2],
                "total_score": r[3], "max_score": r[4],
                "correct_count": r[5], "wrong_count": r[6],
                "accuracy": round(r[3] / r[4] * 100, 1) if r[4] > 0 else 0,
                "completed_at": r[7],
            }
            for r in rows
        ]

    def get_exam_result_detail(self, result_id: int) -> dict:
        with self._lock:
            row = self.conn.execute(
                """SELECT id, session_id, paper_id, paper_title, total_score,
                          max_score, correct_count, wrong_count, completed_at
                   FROM exam_results WHERE id = ?""",
                (result_id,),
            ).fetchone()
            if not row:
                return None

            details = self.conn.execute(
                """SELECT question_id, question_text, selected_answer,
                          correct_answer, is_correct, score, layer, difficulty, chapter,
                          explanation
                   FROM exam_answer_details WHERE result_id = ?
                   ORDER BY id""",
                (result_id,),
            ).fetchall()

        return {
            "result_id": row[0], "session_id": row[1], "paper_id": row[2],
            "paper_title": row[3], "total_score": row[4], "max_score": row[5],
            "correct_count": row[6], "wrong_count": row[7], "completed_at": row[8],
            "accuracy": round(row[4] / row[5] * 100, 1) if row[5] > 0 else 0,
            "details": [
                {
                    "question_id": d[0], "text": d[1],
                    "selected_answer": d[2], "correct_answer": d[3],
                    "correct": bool(d[4]), "score": d[5],
                    "layer": d[6], "difficulty": d[7], "chapter": d[8],
                    "explanation": d[9],
                }
                for d in details
            ],
        }

    def update_weak_point_profile(self, session_id: str):
        """汇总该 session 所有考试的错误记录，更新薄弱点画像"""
        now = datetime.now().isoformat()
        with self._lock:
            # 汇总所有错误按 layer
            for row in self.conn.execute(
                """SELECT ad.layer, COUNT(*) as cnt
                   FROM exam_answer_details ad
                   JOIN exam_results er ON ad.result_id = er.id
                   WHERE er.session_id = ? AND ad.is_correct = 0 AND ad.layer != ''
                   GROUP BY ad.layer""",
                (session_id,),
            ).fetchall():
                self.conn.execute(
                    """INSERT INTO weak_point_profile (session_id, category, category_value, error_count, updated_at)
                       VALUES (?, 'layer', ?, ?, ?)
                       ON CONFLICT(session_id, category, category_value)
                       DO UPDATE SET error_count = excluded.error_count, updated_at = excluded.updated_at""",
                    (session_id, row[0], row[1], now),
                )

            # 汇总所有错误按 difficulty
            for row in self.conn.execute(
                """SELECT ad.difficulty, COUNT(*) as cnt
                   FROM exam_answer_details ad
                   JOIN exam_results er ON ad.result_id = er.id
                   WHERE er.session_id = ? AND ad.is_correct = 0 AND ad.difficulty != ''
                   GROUP BY ad.difficulty""",
                (session_id,),
            ).fetchall():
                self.conn.execute(
                    """INSERT INTO weak_point_profile (session_id, category, category_value, error_count, updated_at)
                       VALUES (?, 'difficulty', ?, ?, ?)
                       ON CONFLICT(session_id, category, category_value)
                       DO UPDATE SET error_count = excluded.error_count, updated_at = excluded.updated_at""",
                    (session_id, row[0], row[1], now),
                )

            # 汇总所有错误按 chapter
            for row in self.conn.execute(
                """SELECT ad.chapter, COUNT(*) as cnt
                   FROM exam_answer_details ad
                   JOIN exam_results er ON ad.result_id = er.id
                   WHERE er.session_id = ? AND ad.is_correct = 0 AND ad.chapter != ''
                   GROUP BY ad.chapter""",
                (session_id,),
            ).fetchall():
                self.conn.execute(
                    """INSERT INTO weak_point_profile (session_id, category, category_value, error_count, updated_at)
                       VALUES (?, 'chapter', ?, ?, ?)
                       ON CONFLICT(session_id, category, category_value)
                       DO UPDATE SET error_count = excluded.error_count, updated_at = excluded.updated_at""",
                    (session_id, row[0], row[1], now),
                )

            self.conn.commit()

    def get_weak_point_profile(self, session_id: str) -> dict:
        with self._lock:
            rows = self.conn.execute(
                """SELECT category, category_value, error_count
                   FROM weak_point_profile
                   WHERE session_id = ?
                   ORDER BY error_count DESC""",
                (session_id,),
            ).fetchall()

        profile = {"layer": [], "difficulty": [], "chapter": []}
        total_errors = sum(r[2] for r in rows)
        for cat, val, cnt in rows:
            pct = round(cnt / total_errors * 100, 1) if total_errors > 0 else 0
            profile[cat].append({"value": val, "error_count": cnt, "percentage": pct})
        return profile

    def get_exam_error_stats(self, session_id: str) -> dict:
        """返回按层次/难度/章节的错误统计（用于前端图表）"""
        with self._lock:
            by_layer = {}
            for row in self.conn.execute(
                """SELECT ad.layer, COUNT(*) as cnt
                   FROM exam_answer_details ad
                   JOIN exam_results er ON ad.result_id = er.id
                   WHERE er.session_id = ? AND ad.is_correct = 0 AND ad.layer != ''
                   GROUP BY ad.layer""",
                (session_id,),
            ).fetchall():
                by_layer[row[0]] = row[1]

            by_difficulty = {}
            for row in self.conn.execute(
                """SELECT ad.difficulty, COUNT(*) as cnt
                   FROM exam_answer_details ad
                   JOIN exam_results er ON ad.result_id = er.id
                   WHERE er.session_id = ? AND ad.is_correct = 0 AND ad.difficulty != ''
                   GROUP BY ad.difficulty""",
                (session_id,),
            ).fetchall():
                by_difficulty[row[0]] = row[1]

            by_chapter = {}
            for row in self.conn.execute(
                """SELECT ad.chapter, COUNT(*) as cnt
                   FROM exam_answer_details ad
                   JOIN exam_results er ON ad.result_id = er.id
                   WHERE er.session_id = ? AND ad.is_correct = 0 AND ad.chapter != ''
                   GROUP BY ad.chapter""",
                (session_id,),
            ).fetchall():
                by_chapter[row[0]] = row[1]

        return {
            "by_layer": by_layer,
            "by_difficulty": by_difficulty,
            "by_chapter": by_chapter,
        }

    def get_last_wrong_details(self, session_id: str) -> list:
        """获取最近一次考试的错误详情（用于 LLM 分析）"""
        with self._lock:
            row = self.conn.execute(
                """SELECT id FROM exam_results
                   WHERE session_id = ?
                   ORDER BY completed_at DESC LIMIT 1""",
                (session_id,),
            ).fetchone()
            if not row:
                return []

            details = self.conn.execute(
                """SELECT question_text, selected_answer, correct_answer,
                          layer, difficulty, chapter
                   FROM exam_answer_details
                   WHERE result_id = ? AND is_correct = 0""",
                (row[0],),
            ).fetchall()

        return [
            {
                "text": d[0], "selected_answer": d[1], "correct_answer": d[2],
                "layer": d[3], "difficulty": d[4], "chapter": d[5],
            }
            for d in details
        ]

    def close(self):
        if self.conn:
            self.conn.close()
            logger.info("MasteryTracker 数据库连接已关闭")
