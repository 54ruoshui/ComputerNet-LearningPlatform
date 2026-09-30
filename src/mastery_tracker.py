"""
学生知识点掌握追踪器
使用 SQLite 存储每个学生对每个实体的掌握状态。
支持基于艾宾浩斯遗忘曲线的复习调度。
"""

import json
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import structlog

from src.services.review_scheduler import (
    MASTERY_HALF_LIFE_THRESHOLD,
    MASTERY_MIN_QUALITY,
    MASTERY_MIN_REVIEWS,
    ReviewScheduler,
    calculate_retention,
    get_retention_label,
)

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
            # migration: add learning-effect evaluation columns if missing
            for ddl in (
                "ALTER TABLE review_events ADD COLUMN predicted_retention REAL",
                "ALTER TABLE review_events ADD COLUMN half_life_after REAL",
                "ALTER TABLE review_events ADD COLUMN elapsed_hours REAL",
                "ALTER TABLE review_events ADD COLUMN chapter TEXT NOT NULL DEFAULT ''",
            ):
                try:
                    self.conn.execute(ddl)
                except Exception:
                    pass
            self.conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_review_events_student
                ON review_events(student_id, recorded_at)
            """)
            self.conn.commit()
            self._init_exam_tables()
            self._init_feedback_table()
            self._init_trial_tables()

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

    def _init_feedback_table(self):
        # NOTE: called from _init_tables() which already holds self._lock
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS answer_feedback (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL DEFAULT '',
                question    TEXT NOT NULL DEFAULT '',
                rating      INTEGER NOT NULL,
                comment     TEXT NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_answer_feedback_session
            ON answer_feedback(session_id, created_at)
        """)
        self.conn.commit()

    def _init_trial_tables(self):
        # NOTE: called from _init_tables() which already holds self._lock
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS trials (
                id         TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                config     TEXT NOT NULL DEFAULT '{}',
                status     TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS trial_participants (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                trial_id   TEXT NOT NULL,
                code       TEXT NOT NULL,
                session_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(trial_id, code)
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS trial_tests (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                trial_id       TEXT NOT NULL,
                participant_id INTEGER NOT NULL,
                phase          TEXT NOT NULL,
                question_ids   TEXT NOT NULL DEFAULT '[]',
                answers        TEXT NOT NULL DEFAULT '{}',
                correct_count  INTEGER NOT NULL DEFAULT 0,
                total_count    INTEGER NOT NULL DEFAULT 0,
                details        TEXT NOT NULL DEFAULT '[]',
                started_at     TEXT NOT NULL DEFAULT (datetime('now')),
                submitted_at   TEXT,
                UNIQUE(participant_id, phase)
            )
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_trial_participants_trial
            ON trial_participants(trial_id)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_trial_tests_trial
            ON trial_tests(trial_id)
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
        chapter: str = "",
    ) -> dict:
        """记录一次复习事件，更新复习状态

        Args:
            student_id: 学生/会话 ID
            entity_name: 知识点实体名
            quality: 回忆质量 0-5
            event_type: 'quiz_correct' | 'quiz_wrong' | 'manual'
            chapter: 章节编码（如 '1.7'），用于章节正确率统计

        Returns:
            更新后的复习状态
        """
        now = datetime.now()
        with self._lock:
            # 读取现有状态
            row = self.conn.execute(
                """SELECT review_count, half_life, ease_factor, last_review_at
                   FROM review_state WHERE student_id = ? AND entity_name = ?""",
                (student_id, entity_name),
            ).fetchone()

            if row:
                review_count, half_life, ease_factor, last_at = row
            else:
                review_count, half_life, ease_factor, last_at = 0, 24.0, 2.5, None

            # 距上次复习的时长（间隔效应输入）
            elapsed_hours = None
            if last_at:
                try:
                    elapsed_hours = (now - datetime.fromisoformat(last_at)).total_seconds() / 3600
                except (ValueError, TypeError):
                    elapsed_hours = None

            # 计算新状态
            new_state = self._scheduler.process_review(
                quality=quality,
                review_count=review_count,
                half_life=half_life,
                ease_factor=ease_factor,
                elapsed_hours=elapsed_hours,
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

            # 记录事件（含预测保留率/新半衰期/章节，用于学习效果评估）
            self.conn.execute(
                """INSERT INTO review_events
                   (student_id, entity_name, event_type, quality,
                    predicted_retention, half_life_after, elapsed_hours, chapter)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    student_id, entity_name, event_type, quality,
                    new_state["retention_at_review"],
                    new_state["half_life"],
                    round(elapsed_hours, 2) if elapsed_hours is not None else None,
                    chapter or "",
                ),
            )

            # 曲线驱动的自动掌握判定
            m_row = self.conn.execute(
                "SELECT mastered FROM mastery WHERE student_id = ? AND entity_name = ?",
                (student_id, entity_name),
            ).fetchone()
            prev_mastered = bool(m_row[0]) if m_row else False
            if quality < 3:
                new_mastered = False
            elif (
                new_state["review_count"] >= MASTERY_MIN_REVIEWS
                and new_state["half_life"] >= MASTERY_HALF_LIFE_THRESHOLD
                and quality >= MASTERY_MIN_QUALITY
            ):
                new_mastered = True
            else:
                new_mastered = prev_mastered

            if m_row is None or new_mastered != prev_mastered:
                self.conn.execute(
                    """INSERT INTO mastery (student_id, entity_name, mastered, updated_at)
                       VALUES (?, ?, ?, datetime('now'))
                       ON CONFLICT(student_id, entity_name)
                       DO UPDATE SET mastered = excluded.mastered, updated_at = datetime('now')""",
                    (student_id, entity_name, 1 if new_mastered else 0),
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
            "mastered": new_mastered,
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

    def get_due_reviews(
        self, student_id: str, lookahead_hours: float = 48, limit: int = 50
    ) -> List[dict]:
        """获取到期或即将到期的复习项，按紧急度降序排列

        Args:
            student_id: 学生 ID
            lookahead_hours: 向前看多少小时（默认 48 小时内的都算即将到期）
            limit: 最多返回条数
        """
        now = datetime.now()
        cutoff = (now + timedelta(hours=lookahead_hours)).isoformat()
        with self._lock:
            rows = self.conn.execute(
                """SELECT entity_name, review_count, last_review_at, next_review_at,
                          half_life, ease_factor
                   FROM review_state
                   WHERE student_id = ? AND next_review_at <= ?
                   ORDER BY next_review_at ASC
                   LIMIT ?""",
                (student_id, cutoff, limit),
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

            # 今日/近 7 天/近 30 天复习次数（recorded_at 为 SQLite UTC 格式，统一用 SQL 截断）
            today_events = self.conn.execute(
                """SELECT COUNT(*) FROM review_events
                   WHERE student_id = ? AND recorded_at >= datetime('now', 'start of day')""",
                (student_id,),
            ).fetchone()[0]
            events_7d = self.conn.execute(
                """SELECT COUNT(*) FROM review_events
                   WHERE student_id = ? AND recorded_at >= datetime('now', '-7 days')""",
                (student_id,),
            ).fetchone()[0]
            events_30d = self.conn.execute(
                """SELECT COUNT(*) FROM review_events
                   WHERE student_id = ? AND recorded_at >= datetime('now', '-30 days')""",
                (student_id,),
            ).fetchone()[0]

            # 平均保留率 + 保留率分布（基于实际经过时间）
            rows = self.conn.execute(
                "SELECT half_life, last_review_at FROM review_state WHERE student_id = ?",
                (student_id,),
            ).fetchall()
            retention_dist = {"牢固": 0, "正常": 0, "薄弱": 0, "需复习": 0}
            if rows:
                total_retention = 0.0
                for hl, last_at in rows:
                    if last_at:
                        try:
                            last_review = datetime.fromisoformat(last_at)
                            elapsed = (now - last_review).total_seconds() / 3600
                        except (ValueError, TypeError):
                            elapsed = 0
                    else:
                        elapsed = 0
                    r = calculate_retention(hl, elapsed)
                    total_retention += r
                    retention_dist[get_retention_label(r)] += 1
                avg_retention = total_retention / len(rows)
            else:
                avg_retention = 0

            # 平均半衰期（当前记忆稳定性）
            avg_hl_row = self.conn.execute(
                "SELECT AVG(half_life) FROM review_state WHERE student_id = ?",
                (student_id,),
            ).fetchone()
            avg_half_life = avg_hl_row[0] or 0.0

            # 按质量分布
            quality_dist = {}
            for q_row in self.conn.execute(
                "SELECT quality, COUNT(*) as cnt FROM review_events WHERE student_id = ? GROUP BY quality",
                (student_id,),
            ).fetchall():
                quality_dist[str(q_row[0])] = q_row[1]

            # 回忆成功率：全程 + 近 7 天 vs 前 7 天
            sr_row = self.conn.execute(
                """SELECT
                       AVG(CASE WHEN quality >= 3 THEN 1.0 ELSE 0.0 END),
                       AVG(CASE WHEN recorded_at >= datetime('now', '-7 days')
                                THEN CASE WHEN quality >= 3 THEN 1.0 ELSE 0.0 END END),
                       AVG(CASE WHEN recorded_at < datetime('now', '-7 days')
                                 AND recorded_at >= datetime('now', '-14 days')
                                THEN CASE WHEN quality >= 3 THEN 1.0 ELSE 0.0 END END)
                   FROM review_events WHERE student_id = ?""",
                (student_id,),
            ).fetchone()
            success_rate = round(sr_row[0], 4) if sr_row[0] is not None else 0.0
            success_rate_7d = round(sr_row[1], 4) if sr_row[1] is not None else None
            success_rate_prev_7d = round(sr_row[2], 4) if sr_row[2] is not None else None

            # 稳定性趋势：近 7 天 vs 前 7 天复习后的平均半衰期
            st_row = self.conn.execute(
                """SELECT
                       AVG(CASE WHEN recorded_at >= datetime('now', '-7 days')
                                THEN half_life_after END),
                       AVG(CASE WHEN recorded_at < datetime('now', '-7 days')
                                 AND recorded_at >= datetime('now', '-14 days')
                                THEN half_life_after END)
                   FROM review_events
                   WHERE student_id = ? AND half_life_after IS NOT NULL""",
                (student_id,),
            ).fetchone()
            stability_trend = {
                "recent_avg_half_life": round(st_row[0], 2) if st_row[0] is not None else None,
                "prev_avg_half_life": round(st_row[1], 2) if st_row[1] is not None else None,
            }

            # 校准分析：成功/遗忘复习时的平均预测保留率
            cal_row = self.conn.execute(
                """SELECT
                       AVG(CASE WHEN quality >= 3 THEN predicted_retention END),
                       AVG(CASE WHEN quality < 3 THEN predicted_retention END),
                       COUNT(predicted_retention)
                   FROM review_events
                   WHERE student_id = ? AND predicted_retention IS NOT NULL""",
                (student_id,),
            ).fetchone()
            calibration = {
                "avg_predicted_when_recalled": round(cal_row[0], 4) if cal_row[0] is not None else None,
                "avg_predicted_when_lapsed": round(cal_row[1], 4) if cal_row[1] is not None else None,
                "sampled_events": cal_row[2],
            }

            # 每日趋势（近 30 天）
            daily_trend = []
            for d_row in self.conn.execute(
                """SELECT date(recorded_at) as day, COUNT(*) as cnt
                   FROM review_events
                   WHERE student_id = ? AND recorded_at >= datetime('now', '-30 days')
                   GROUP BY day ORDER BY day""",
                (student_id,),
            ).fetchall():
                daily_trend.append({"date": d_row[0], "count": d_row[1]})

            # 未来 7 天复习负荷预测（过期的计入今天）
            forecast_7d = [
                {"date": (now + timedelta(days=i)).strftime("%Y-%m-%d"), "count": 0, "is_today": i == 0}
                for i in range(7)
            ]
            bucket_by_date = {b["date"]: b for b in forecast_7d}
            for (next_at,) in self.conn.execute(
                """SELECT next_review_at FROM review_state
                   WHERE student_id = ? AND next_review_at <= ?""",
                (student_id, (now + timedelta(days=7)).isoformat()),
            ).fetchall():
                try:
                    next_dt = datetime.fromisoformat(next_at)
                except (ValueError, TypeError):
                    continue
                if next_dt <= now:
                    forecast_7d[0]["count"] += 1
                else:
                    day_str = next_dt.strftime("%Y-%m-%d")
                    if day_str in bucket_by_date:
                        bucket_by_date[day_str]["count"] += 1

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
            "success_rate": success_rate,
            "success_rate_7d": success_rate_7d,
            "success_rate_prev_7d": success_rate_prev_7d,
            "retention_distribution": retention_dist,
            "avg_half_life": round(avg_half_life, 2),
            "avg_half_life_days": round(avg_half_life / 24, 1),
            "stability_trend": stability_trend,
            "forecast_7d": forecast_7d,
            "calibration": calibration,
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

    # ==================== 仪表盘数据 ====================

    def get_recent_review_events(self, student_id: str, limit: int = 20) -> list:
        """最近复习事件（用于学习活动时间线）"""
        with self._lock:
            rows = self.conn.execute(
                """SELECT event_type, entity_name, quality, recorded_at
                   FROM review_events
                   WHERE student_id = ?
                   ORDER BY recorded_at DESC, id DESC
                   LIMIT ?""",
                (student_id, limit),
            ).fetchall()
        return [
            {
                "event_type": r[0], "entity_name": r[1],
                "quality": r[2], "recorded_at": r[3],
            }
            for r in rows
        ]

    def get_chapter_accuracy(self, session_id: str) -> dict:
        """各章节答题正确率（考试明细 + 刷题事件合并）"""
        merged: dict = {}
        with self._lock:
            # 考试明细
            for chapter, total, correct in self.conn.execute(
                """SELECT ad.chapter, COUNT(*), SUM(ad.is_correct)
                   FROM exam_answer_details ad
                   JOIN exam_results er ON ad.result_id = er.id
                   WHERE er.session_id = ? AND ad.chapter != ''
                   GROUP BY ad.chapter""",
                (session_id,),
            ).fetchall():
                merged[chapter] = {"total": total, "correct": correct or 0}

            # 刷题事件（quality>=3 即答对）
            for chapter, total, correct in self.conn.execute(
                """SELECT chapter, COUNT(*), SUM(quality >= 3)
                   FROM review_events
                   WHERE student_id = ? AND chapter != ''
                     AND event_type IN ('quiz_correct', 'quiz_wrong')
                   GROUP BY chapter""",
                (session_id,),
            ).fetchall():
                if chapter in merged:
                    merged[chapter]["total"] += total
                    merged[chapter]["correct"] += correct or 0
                else:
                    merged[chapter] = {"total": total, "correct": correct or 0}
        return merged

    def get_learning_entities(self, student_id: str) -> set:
        """有复习记录但未掌握的实体（学习中）"""
        with self._lock:
            rows = self.conn.execute(
                "SELECT entity_name FROM review_state WHERE student_id = ?",
                (student_id,),
            ).fetchall()
        return {r[0] for r in rows}

    # ==================== 答案满意度反馈 ====================

    def record_feedback(self, session_id: str, rating: int, question: str = "", comment: str = "") -> int:
        """记录一次答案满意度反馈，返回行 id；rating 仅接受 1（满意）/ 0（不满意）"""
        if rating not in (0, 1):
            raise ValueError(f"rating 必须是 0 或 1，得到: {rating}")
        with self._lock:
            cursor = self.conn.execute(
                """INSERT INTO answer_feedback (session_id, question, rating, comment)
                   VALUES (?, ?, ?, ?)""",
                (session_id or "", (question or "")[:2000], rating, (comment or "")[:500]),
            )
            self.conn.commit()
            return cursor.lastrowid

    def get_feedback_stats(self, session_id: str = None) -> dict:
        """满意度统计；session_id 为 None 时返回全局统计（对比实验报告数据源）"""
        where, params = ("WHERE session_id = ?", (session_id,)) if session_id else ("", ())
        with self._lock:
            total, positive = self.conn.execute(
                f"SELECT COUNT(*), SUM(rating) FROM answer_feedback {where}", params
            ).fetchone()
            by_day = [
                {"date": r[0], "positive": r[1] or 0, "total": r[2]}
                for r in self.conn.execute(
                    f"""SELECT date(created_at), SUM(rating), COUNT(*) FROM answer_feedback {where}
                        GROUP BY date(created_at) ORDER BY date(created_at)""",
                    params,
                )
            ]
        total = total or 0
        positive = positive or 0
        return {
            "total": total,
            "positive": positive,
            "negative": total - positive,
            "satisfaction_rate": round(positive / total, 4) if total else 0.0,
            "by_day": by_day,
        }

    # ==================== 前测-后测试用评估 ====================

    def save_trial(self, trial_id: str, name: str, config: dict) -> str:
        """创建试用批次，config 为 JSON 可序列化 dict（含分层抽题结果）"""
        with self._lock:
            self.conn.execute(
                """INSERT INTO trials (id, name, config) VALUES (?, ?, ?)""",
                (trial_id, name, json.dumps(config, ensure_ascii=False)),
            )
            self.conn.commit()
        return trial_id

    def get_trial(self, trial_id: str) -> Optional[dict]:
        with self._lock:
            row = self.conn.execute(
                "SELECT id, name, config, status, created_at FROM trials WHERE id = ?",
                (trial_id,),
            ).fetchone()
        if not row:
            return None
        try:
            config = json.loads(row[2])
        except (ValueError, TypeError):
            config = {}
        return {
            "id": row[0], "name": row[1], "config": config,
            "status": row[3], "created_at": row[4],
        }

    def list_trials(self) -> list:
        with self._lock:
            rows = self.conn.execute(
                """SELECT t.id, t.name, t.status, t.created_at,
                          (SELECT COUNT(*) FROM trial_participants p WHERE p.trial_id = t.id),
                          (SELECT COUNT(*) FROM trial_tests tt
                           WHERE tt.trial_id = t.id AND tt.submitted_at IS NOT NULL)
                   FROM trials t ORDER BY t.created_at DESC"""
            ).fetchall()
        return [
            {
                "id": r[0], "name": r[1], "status": r[2], "created_at": r[3],
                "participant_count": r[4], "submitted_test_count": r[5],
            }
            for r in rows
        ]

    def add_trial_participant(self, trial_id: str, code: str) -> dict:
        """登记被试；同一批次内 code 重复则抛 ValueError"""
        try:
            with self._lock:
                cursor = self.conn.execute(
                    """INSERT INTO trial_participants (trial_id, code) VALUES (?, ?)""",
                    (trial_id, code),
                )
                self.conn.commit()
                return {"id": cursor.lastrowid, "trial_id": trial_id, "code": code}
        except sqlite3.IntegrityError as e:
            raise ValueError(f"被试码 {code} 在该批次已存在") from e

    def bind_participant_session(self, participant_id: int, session_id: str):
        with self._lock:
            self.conn.execute(
                "UPDATE trial_participants SET session_id = ? WHERE id = ?",
                (session_id, participant_id),
            )
            self.conn.commit()

    def get_participant_by_code(self, code: str, active_only: bool = True) -> Optional[dict]:
        """按被试码查参与者（join 批次状态，默认只在 active 批次中查找）"""
        query = """
            SELECT p.id, p.trial_id, p.code, p.session_id, p.created_at, t.status, t.name
            FROM trial_participants p JOIN trials t ON p.trial_id = t.id
            WHERE p.code = ?
        """
        params = [code]
        if active_only:
            query += " AND t.status = 'active'"
        query += " ORDER BY p.created_at DESC LIMIT 1"
        with self._lock:
            row = self.conn.execute(query, params).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "trial_id": row[1], "code": row[2],
            "session_id": row[3], "created_at": row[4],
            "trial_status": row[5], "trial_name": row[6],
        }

    def get_participant(self, participant_id: int) -> Optional[dict]:
        with self._lock:
            row = self.conn.execute(
                """SELECT p.id, p.trial_id, p.code, p.session_id, p.created_at
                   FROM trial_participants p WHERE p.id = ?""",
                (participant_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "trial_id": row[1], "code": row[2],
            "session_id": row[3], "created_at": row[4],
        }

    def list_participants(self, trial_id: str) -> list:
        """被试列表，含前测/后测完成状态"""
        with self._lock:
            rows = self.conn.execute(
                """SELECT p.id, p.code, p.session_id, p.created_at,
                          MAX(CASE WHEN tt.phase = 'pre' AND tt.submitted_at IS NOT NULL
                              THEN 1 ELSE 0 END),
                          MAX(CASE WHEN tt.phase = 'post' AND tt.submitted_at IS NOT NULL
                              THEN 1 ELSE 0 END)
                   FROM trial_participants p
                   LEFT JOIN trial_tests tt ON tt.participant_id = p.id
                   WHERE p.trial_id = ?
                   GROUP BY p.id ORDER BY p.id""",
                (trial_id,),
            ).fetchall()
        return [
            {
                "id": r[0], "code": r[1], "session_id": r[2], "created_at": r[3],
                "pre_done": bool(r[4]), "post_done": bool(r[5]),
            }
            for r in rows
        ]

    def upsert_trial_test(
        self, trial_id: str, participant_id: int, phase: str, question_ids: list
    ) -> dict:
        """开始一次阶段测试；未提交的已有记录幂等返回，已提交则拒绝"""
        with self._lock:
            row = self.conn.execute(
                """SELECT id, question_ids, submitted_at FROM trial_tests
                   WHERE participant_id = ? AND phase = ?""",
                (participant_id, phase),
            ).fetchone()
            if row:
                if row[2]:
                    raise ValueError(f"该被试的 {phase} 测试已提交，不可重复作答")
                return {"test_id": row[0], "question_ids": json.loads(row[1])}
            cursor = self.conn.execute(
                """INSERT INTO trial_tests (trial_id, participant_id, phase, question_ids)
                   VALUES (?, ?, ?, ?)""",
                (trial_id, participant_id, phase, json.dumps(question_ids)),
            )
            self.conn.commit()
            return {"test_id": cursor.lastrowid, "question_ids": question_ids}

    def save_trial_test_submit(
        self, test_id: int, answers: dict, correct_count: int, total_count: int, details: list
    ) -> dict:
        now = datetime.now().isoformat()
        with self._lock:
            row = self.conn.execute(
                "SELECT submitted_at FROM trial_tests WHERE id = ?", (test_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"测试记录 {test_id} 不存在")
            if row[0]:
                raise ValueError("该测试已提交，不可重复提交")
            self.conn.execute(
                """UPDATE trial_tests
                   SET answers = ?, correct_count = ?, total_count = ?, details = ?,
                       submitted_at = ?
                   WHERE id = ?""",
                (
                    json.dumps(answers, ensure_ascii=False), correct_count, total_count,
                    json.dumps(details, ensure_ascii=False), now, test_id,
                ),
            )
            self.conn.commit()
        return {"test_id": test_id, "correct_count": correct_count, "total_count": total_count}

    def get_trial_test(self, test_id: int) -> Optional[dict]:
        with self._lock:
            row = self.conn.execute(
                """SELECT id, trial_id, participant_id, phase, question_ids, answers,
                          correct_count, total_count, details, started_at, submitted_at
                   FROM trial_tests WHERE id = ?""",
                (test_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "test_id": row[0], "trial_id": row[1], "participant_id": row[2], "phase": row[3],
            "question_ids": json.loads(row[4]),
            "answers": json.loads(row[5]) if row[5] else {},
            "correct_count": row[6], "total_count": row[7],
            "details": json.loads(row[8]) if row[8] else [],
            "started_at": row[9], "submitted_at": row[10],
        }

    def get_trial_tests(self, trial_id: str) -> list:
        with self._lock:
            rows = self.conn.execute(
                """SELECT id, participant_id, phase, question_ids, answers,
                          correct_count, total_count, details, started_at, submitted_at
                   FROM trial_tests WHERE trial_id = ? ORDER BY participant_id, phase""",
                (trial_id,),
            ).fetchall()
        return [
            {
                "test_id": r[0], "participant_id": r[1], "phase": r[2],
                "question_ids": json.loads(r[3]),
                "answers": json.loads(r[4]) if r[4] else {},
                "correct_count": r[5], "total_count": r[6],
                "details": json.loads(r[7]) if r[7] else [],
                "started_at": r[8], "submitted_at": r[9],
            }
            for r in rows
        ]

    def get_review_behavior(self, session_id: str) -> dict:
        """按 event_type 汇总该 session 的学习行为（答题/复习次数、覆盖知识点数）"""
        with self._lock:
            rows = self.conn.execute(
                """SELECT event_type, COUNT(*), COUNT(DISTINCT entity_name)
                   FROM review_events WHERE student_id = ? GROUP BY event_type""",
                (session_id,),
            ).fetchall()
            distinct = self.conn.execute(
                """SELECT COUNT(DISTINCT entity_name) FROM review_events
                   WHERE student_id = ?""",
                (session_id,),
            ).fetchone()
        behavior = {}
        total_events = 0
        for event_type, cnt, entities in rows:
            behavior[event_type] = {"count": cnt, "entities": entities}
            total_events += cnt
        return {
            "by_type": behavior,
            "total_events": total_events,
            "total_entities": distinct[0] if distinct else 0,
        }

    def close(self):
        if self.conn:
            self.conn.close()
            logger.info("MasteryTracker 数据库连接已关闭")
