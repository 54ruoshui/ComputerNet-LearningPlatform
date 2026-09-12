"""MasteryTracker 单元测试 — 复习调度存储、曲线驱动掌握、效果评估统计"""

from datetime import datetime, timedelta

import pytest

from src.mastery_tracker import MasteryTracker


@pytest.fixture
def tracker(tmp_path):
    t = MasteryTracker(db_path=str(tmp_path / "mastery.db"))
    yield t
    t.close()


SID = "student-1"
ENTITY = "TCP"


def seed_review_state(tracker, entity=ENTITY, review_count=3, half_life=600.0,
                      ease_factor=2.5, last_review_at=None, next_review_at=None):
    """直接写入复习状态（绕过调度器，用于构造边界场景）"""
    tracker.conn.execute(
        """INSERT INTO review_state
           (student_id, entity_name, review_count, last_review_at, next_review_at,
            half_life, ease_factor)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(student_id, entity_name) DO UPDATE SET
               review_count = excluded.review_count,
               last_review_at = excluded.last_review_at,
               next_review_at = excluded.next_review_at,
               half_life = excluded.half_life,
               ease_factor = excluded.ease_factor""",
        (SID, entity, review_count, last_review_at, next_review_at, half_life, ease_factor),
    )
    tracker.conn.commit()


class TestRecordReview:
    def test_first_review_logs_evaluation_columns(self, tracker):
        result = tracker.record_review(SID, ENTITY, quality=4, event_type="manual")

        row = tracker.conn.execute(
            """SELECT predicted_retention, half_life_after, elapsed_hours
               FROM review_events WHERE student_id = ? AND entity_name = ?""",
            (SID, ENTITY),
        ).fetchone()
        assert row is not None
        assert row[0] == pytest.approx(0.5)  # 首次学习假定保留率
        assert row[1] == pytest.approx(84.0, abs=0.01)  # 24 × 3.5
        assert row[2] is None  # 无历史

        assert result["mastered"] is False
        assert result["review_count"] == 1

    def test_elapsed_logged_and_stability_grows(self, tracker):
        tracker.record_review(SID, ENTITY, quality=4)
        # 回拨上次复习时间到 20 小时前（模拟按时到期复习）
        past = (datetime.now() - timedelta(hours=20)).isoformat()
        tracker.conn.execute(
            "UPDATE review_state SET last_review_at = ? WHERE student_id = ? AND entity_name = ?",
            (past, SID, ENTITY),
        )
        tracker.conn.commit()

        before = tracker.conn.execute(
            "SELECT half_life FROM review_state WHERE student_id = ? AND entity_name = ?",
            (SID, ENTITY),
        ).fetchone()[0]
        result = tracker.record_review(SID, ENTITY, quality=4)
        after = result["half_life"]

        assert after > before
        evt = tracker.conn.execute(
            "SELECT elapsed_hours FROM review_events WHERE student_id = ? ORDER BY id DESC LIMIT 1",
            (SID,),
        ).fetchone()
        assert evt[0] == pytest.approx(20.0, abs=0.5)

    def test_immediate_repeat_barely_grows(self, tracker):
        r1 = tracker.record_review(SID, ENTITY, quality=4)
        r2 = tracker.record_review(SID, ENTITY, quality=4)
        # 间隔效应：紧连重复 elapsed≈0 → R≈1 → 增益≈0
        assert r2["half_life"] == pytest.approx(r1["half_life"], rel=0.02)


class TestCurveDrivenMastery:
    def test_mastered_when_threshold_met(self, tracker):
        seed_review_state(tracker, review_count=3, half_life=600.0)
        result = tracker.record_review(SID, ENTITY, quality=4)
        assert result["mastered"] is True
        assert ENTITY in tracker.get_mastery(SID)
        assert tracker.get_mastery(SID)[ENTITY] is True

    def test_lapse_unmasters(self, tracker):
        seed_review_state(tracker, review_count=5, half_life=800.0)
        tracker.record_review(SID, ENTITY, quality=4)  # 达标掌握
        result = tracker.record_review(SID, ENTITY, quality=1)
        assert result["mastered"] is False
        assert tracker.get_mastery(SID)[ENTITY] is False

    def test_not_mastered_below_review_count(self, tracker):
        # 判定用复习后计数：seed=1 + 本次 = 2 次 < 3
        seed_review_state(tracker, review_count=1, half_life=600.0)
        result = tracker.record_review(SID, ENTITY, quality=4)
        assert result["mastered"] is False

    def test_not_mastered_below_half_life(self, tracker):
        seed_review_state(tracker, review_count=3, half_life=100.0)
        result = tracker.record_review(SID, ENTITY, quality=4)
        assert result["mastered"] is False

    def test_not_mastered_low_quality(self, tracker):
        seed_review_state(tracker, review_count=3, half_life=600.0)
        result = tracker.record_review(SID, ENTITY, quality=3)
        assert result["mastered"] is False

    def test_manual_override_preserved_on_neutral_review(self, tracker):
        tracker.set_mastery(SID, ENTITY, True)
        seed_review_state(tracker, review_count=1, half_life=50.0)
        result = tracker.record_review(SID, ENTITY, quality=4)
        assert result["mastered"] is True  # 未达授予条件也不翻转手动设置


class TestDueReviews:
    def test_ordering_and_lookahead(self, tracker):
        now = datetime.now()
        seed_review_state(tracker, "A", 1, 100.0,
                          next_review_at=(now - timedelta(hours=10)).isoformat())
        seed_review_state(tracker, "B", 1, 100.0,
                          next_review_at=(now + timedelta(hours=2)).isoformat())
        seed_review_state(tracker, "C", 1, 100.0,
                          next_review_at=(now + timedelta(hours=100)).isoformat())

        due = tracker.get_due_reviews(SID, lookahead_hours=48)
        names = [d["entity_name"] for d in due]
        assert "A" in names and "B" in names and "C" not in names
        assert names.index("A") < names.index("B")  # 过期更久 → 更紧急

    def test_limit(self, tracker):
        now = datetime.now()
        for i in range(10):
            seed_review_state(tracker, f"E{i}", 1, 100.0,
                              next_review_at=(now - timedelta(hours=i + 1)).isoformat())
        assert len(tracker.get_due_reviews(SID, lookahead_hours=48, limit=5)) == 5


class TestReviewStats:
    LEGACY_KEYS = (
        "total_entities", "total_reviews", "today_reviews", "reviews_7d",
        "reviews_30d", "due_now", "avg_retention", "quality_distribution", "daily_trend",
    )
    NEW_KEYS = (
        "success_rate", "success_rate_7d", "success_rate_prev_7d",
        "retention_distribution", "avg_half_life", "avg_half_life_days",
        "stability_trend", "forecast_7d", "calibration",
    )

    def test_empty_student(self, tracker):
        stats = tracker.get_review_stats("nobody")
        for key in self.LEGACY_KEYS + self.NEW_KEYS:
            assert key in stats
        assert stats["total_entities"] == 0
        assert stats["success_rate"] == 0.0
        assert stats["calibration"]["sampled_events"] == 0
        assert len(stats["forecast_7d"]) == 7

    def test_stats_after_reviews(self, tracker):
        seed_review_state(tracker, review_count=3, half_life=600.0)
        tracker.record_review(SID, ENTITY, quality=4)
        tracker.record_review(SID, ENTITY, quality=1)

        stats = tracker.get_review_stats(SID)
        assert stats["total_entities"] == 1
        assert stats["total_reviews"] == 2
        assert stats["success_rate"] == 0.5
        assert stats["retention_distribution"]["牢固"] == 1  # S 大且刚复习
        assert stats["avg_half_life"] > 0
        assert stats["calibration"]["sampled_events"] == 2
        assert stats["calibration"]["avg_predicted_when_lapsed"] is not None
        assert len(stats["forecast_7d"]) == 7
        assert stats["forecast_7d"][0]["is_today"] is True

    def test_forecast_buckets_overdue_into_today(self, tracker):
        now = datetime.now()
        seed_review_state(tracker, "A", 1, 100.0,
                          next_review_at=(now - timedelta(hours=5)).isoformat())
        seed_review_state(tracker, "B", 1, 100.0,
                          next_review_at=(now + timedelta(days=3)).isoformat())
        stats = tracker.get_review_stats(SID)
        assert stats["forecast_7d"][0]["count"] == 1
        assert stats["forecast_7d"][3]["count"] == 1


class TestChapterAccuracy:
    def test_chapter_stored_on_event(self, tracker):
        tracker.record_review(SID, ENTITY, quality=4, event_type="quiz_correct", chapter="1.7")
        row = tracker.conn.execute(
            "SELECT chapter FROM review_events WHERE student_id = ? ORDER BY id DESC LIMIT 1",
            (SID,),
        ).fetchone()
        assert row[0] == "1.7"

    def test_manual_review_without_chapter(self, tracker):
        tracker.record_review(SID, ENTITY, quality=4)
        row = tracker.conn.execute(
            "SELECT chapter FROM review_events WHERE student_id = ? ORDER BY id DESC LIMIT 1",
            (SID,),
        ).fetchone()
        assert row[0] == ""

    def test_accuracy_merges_quiz_and_exam(self, tracker):
        # 刷题：1.7 对 1 次（quality 4）、错 1 次（quality 1）
        tracker.record_review(SID, "IP", 4, "quiz_correct", chapter="1.7")
        tracker.record_review(SID, "IP", 1, "quiz_wrong", chapter="1.7")
        # 刷题：3.1 对 1 次
        tracker.record_review(SID, "交换机", 5, "quiz_correct", chapter="3.1")
        # 手动复习（无章节）不参与统计
        tracker.record_review(SID, "TCP", 4, "manual", chapter="")

        result = tracker.get_chapter_accuracy(SID)
        assert result["1.7"] == {"total": 2, "correct": 1}
        assert result["3.1"] == {"total": 1, "correct": 1}
        assert "TCP" not in result

    def test_exam_and_quiz_merge_sums(self, tracker):
        # 模拟一条考试明细：1.7 对 3/4
        cursor = tracker.conn.execute(
            "INSERT INTO exam_results (session_id, paper_id, paper_title) VALUES (?, 'p', 't')",
            (SID,),
        )
        tracker.conn.execute(
            "INSERT INTO exam_answer_details (result_id, question_id, chapter, is_correct) VALUES "
            "(?, 'q1', '1.7', 1), (?, 'q2', '1.7', 1), (?, 'q3', '1.7', 1), (?, 'q4', '1.7', 0)",
            (cursor.lastrowid,) * 4,
        )
        tracker.conn.commit()
        # 刷题：1.7 对 1/2
        tracker.record_review(SID, "IP", 4, "quiz_correct", chapter="1.7")
        tracker.record_review(SID, "IP", 1, "quiz_wrong", chapter="1.7")

        result = tracker.get_chapter_accuracy(SID)
        assert result["1.7"] == {"total": 6, "correct": 4}


class TestMigration:
    def test_reinstantiate_is_idempotent(self, tmp_path):
        db = str(tmp_path / "mastery.db")
        t1 = MasteryTracker(db_path=db)
        t1.record_review(SID, ENTITY, quality=4)
        t1.close()

        t2 = MasteryTracker(db_path=db)  # 不应抛异常
        try:
            cols = {r[1] for r in t2.conn.execute("PRAGMA table_info(review_events)").fetchall()}
            assert {"predicted_retention", "half_life_after", "elapsed_hours"} <= cols
        finally:
            t2.close()

    def test_migrates_legacy_db(self, tmp_path):
        """旧库（无新列）首次实例化时自动补列"""
        import sqlite3
        db = str(tmp_path / "legacy.db")
        conn = sqlite3.connect(db)
        conn.execute(
            """CREATE TABLE review_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, student_id TEXT NOT NULL,
                entity_name TEXT NOT NULL, event_type TEXT NOT NULL,
                quality INTEGER NOT NULL DEFAULT 0,
                recorded_at TEXT NOT NULL DEFAULT (datetime('now')))"""
        )
        conn.commit()
        conn.close()

        t = MasteryTracker(db_path=db)
        try:
            t.record_review(SID, ENTITY, quality=5)  # 新列可写
            row = t.conn.execute(
                "SELECT half_life_after FROM review_events WHERE student_id = ?", (SID,)
            ).fetchone()
            assert row[0] is not None
        finally:
            t.close()
