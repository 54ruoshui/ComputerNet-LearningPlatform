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


class TestAnswerFeedback:
    def test_record_and_stats(self, tracker):
        tracker.record_feedback(SID, rating=1, question="TCP 三次握手")
        tracker.record_feedback(SID, rating=1)
        tracker.record_feedback(SID, rating=0, question="UDP")

        stats = tracker.get_feedback_stats(SID)
        assert stats["total"] == 3
        assert stats["positive"] == 2
        assert stats["negative"] == 1
        assert stats["satisfaction_rate"] == pytest.approx(0.6667, abs=0.0001)
        assert len(stats["by_day"]) == 1

    def test_invalid_rating_raises(self, tracker):
        with pytest.raises(ValueError):
            tracker.record_feedback(SID, rating=2)

    def test_global_stats_aggregate_sessions(self, tracker):
        tracker.record_feedback("s1", rating=1)
        tracker.record_feedback("s2", rating=0)

        stats = tracker.get_feedback_stats()
        assert stats["total"] == 2
        assert stats["positive"] == 1
        assert stats["satisfaction_rate"] == 0.5

    def test_empty_stats(self, tracker):
        stats = tracker.get_feedback_stats("nobody")
        assert stats["total"] == 0
        assert stats["satisfaction_rate"] == 0.0
        assert stats["by_day"] == []


class TestTrialTables:
    def test_tables_auto_created(self, tmp_path):
        t = MasteryTracker(db_path=str(tmp_path / "mastery.db"))
        names = {
            r[0]
            for r in t.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        t.close()
        assert {"trials", "trial_participants", "trial_tests"} <= names

    def test_save_and_get_trial(self, tracker):
        config = {"per_layer": 4, "seed": 42, "pre_ids": ["a"], "post_ids": ["b"]}
        tracker.save_trial("tr-1", "第一批试用", config)

        got = tracker.get_trial("tr-1")
        assert got["name"] == "第一批试用"
        assert got["config"] == config
        assert got["status"] == "active"
        assert tracker.get_trial("nope") is None

        trials = tracker.list_trials()
        assert len(trials) == 1
        assert trials[0]["participant_count"] == 0

    def test_add_participant_duplicate_rejected(self, tracker):
        tracker.save_trial("tr-1", "t", {})
        p = tracker.add_trial_participant("tr-1", "P0001")
        assert p["id"] == 1 and p["code"] == "P0001"

        with pytest.raises(ValueError):
            tracker.add_trial_participant("tr-1", "P0001")
        # 不同批次允许同码
        tracker.save_trial("tr-2", "t2", {})
        assert tracker.add_trial_participant("tr-2", "P0001")["id"] == 2

    def test_bind_session_and_lookup_by_code(self, tracker):
        tracker.save_trial("tr-1", "t", {})
        pid = tracker.add_trial_participant("tr-1", "P0001")["id"]

        assert tracker.get_participant_by_code("P0001")["id"] == pid
        assert tracker.get_participant_by_code("PXXXX") is None

        tracker.bind_participant_session(pid, "sess-9")
        found = tracker.get_participant_by_code("P0001")
        assert found["session_id"] == "sess-9"
        assert tracker.get_participant(pid)["session_id"] == "sess-9"

    def test_upsert_trial_test_idempotent_until_submitted(self, tracker):
        tracker.save_trial("tr-1", "t", {})
        pid = tracker.add_trial_participant("tr-1", "P0001")["id"]

        first = tracker.upsert_trial_test("tr-1", pid, "pre", ["q1", "q2"])
        second = tracker.upsert_trial_test("tr-1", pid, "pre", ["q1", "q2"])
        assert first["test_id"] == second["test_id"]

        tracker.save_trial_test_submit(first["test_id"], {"q1": "A"}, 1, 2, [])
        with pytest.raises(ValueError, match="已提交"):
            tracker.upsert_trial_test("tr-1", pid, "pre", ["q1", "q2"])

    def test_submit_persists_and_blocks_resubmit(self, tracker):
        tracker.save_trial("tr-1", "t", {})
        pid = tracker.add_trial_participant("tr-1", "P0001")["id"]
        test_id = tracker.upsert_trial_test("tr-1", pid, "post", ["q1", "q2", "q3"])["test_id"]

        details = [{"question_id": "q1", "correct": True}]
        result = tracker.save_trial_test_submit(
            test_id, {"q1": "A", "q2": "B"}, 2, 3, details
        )
        assert result == {"test_id": test_id, "correct_count": 2, "total_count": 3}

        stored = tracker.get_trial_test(test_id)
        assert stored["submitted_at"] is not None
        assert stored["correct_count"] == 2
        assert stored["details"] == details

        with pytest.raises(ValueError, match="已提交"):
            tracker.save_trial_test_submit(test_id, {}, 0, 3, [])
        with pytest.raises(ValueError):
            tracker.save_trial_test_submit(9999, {}, 0, 0, [])

    def test_list_participants_marks_completion(self, tracker):
        tracker.save_trial("tr-1", "t", {})
        p1 = tracker.add_trial_participant("tr-1", "P0001")["id"]
        tracker.add_trial_participant("tr-1", "P0002")

        pre = tracker.upsert_trial_test("tr-1", p1, "pre", ["q1"])["test_id"]
        tracker.save_trial_test_submit(pre, {"q1": "A"}, 1, 1, [])
        post = tracker.upsert_trial_test("tr-1", p1, "post", ["q2"])["test_id"]
        tracker.upsert_trial_test("tr-1", p1, "post", ["q2"])  # 幂等

        participants = tracker.list_participants("tr-1")
        by_code = {p["code"]: p for p in participants}
        assert by_code["P0001"]["pre_done"] is True
        assert by_code["P0001"]["post_done"] is False  # 已开始未提交
        assert by_code["P0002"]["pre_done"] is False

        assert len(tracker.get_trial_tests("tr-1")) == 2

    def test_review_behavior_aggregates(self, tracker):
        tracker.record_review(SID, "TCP", quality=4, event_type="quiz_correct")
        tracker.record_review(SID, "UDP", quality=4, event_type="quiz_correct")
        tracker.record_review(SID, "TCP", quality=1, event_type="quiz_wrong")

        behavior = tracker.get_review_behavior(SID)
        assert behavior["total_events"] == 3
        assert behavior["total_entities"] == 2
        assert behavior["by_type"]["quiz_correct"]["count"] == 2
        assert behavior["by_type"]["quiz_wrong"]["count"] == 1


class TestQuizStorage:
    """quiz_done / quiz_wrong 表：按 session 隔离的已做/错题记录"""

    ENTRY = {
        "id": "mcq_001",
        "text": "TCP 建立连接需要几次握手？",
        "option_a": "2 次", "option_b": "3 次", "option_c": "4 次", "option_d": "5 次",
        "correct_answer": "B", "explanation": "SYN / SYN+ACK / ACK",
        "layer": "传输层", "difficulty": "basic", "selected": "A",
        "time": "2026-09-30T10:00:00",
    }

    def test_done_mark_and_list(self, tracker):
        assert tracker.get_quiz_done_ids(SID) == []
        ids = tracker.mark_quiz_done(SID, "mcq_001")
        assert ids == ["mcq_001"]
        tracker.mark_quiz_done(SID, "mcq_002")
        tracker.mark_quiz_done(SID, "mcq_001")  # 幂等
        assert tracker.get_quiz_done_ids(SID) == ["mcq_001", "mcq_002"]

    def test_done_isolated_by_session(self, tracker):
        tracker.mark_quiz_done(SID, "mcq_001")
        tracker.mark_quiz_done("other", "mcq_002")
        assert tracker.get_quiz_done_ids(SID) == ["mcq_001"]
        assert tracker.get_quiz_done_ids("other") == ["mcq_002"]

    def test_wrong_upsert_newest_first_and_overwrite(self, tracker):
        lst = tracker.upsert_quiz_wrong(SID, dict(self.ENTRY))
        assert len(lst) == 1 and lst[0]["id"] == "mcq_001"
        assert lst[0]["correct_answer"] == "B"
        assert lst[0]["time"] == self.ENTRY["time"]

        # 新错题插到队首
        entry2 = dict(self.ENTRY, id="mcq_002", text="UDP 首部固定长度？")
        lst = tracker.upsert_quiz_wrong(SID, entry2)
        assert [w["id"] for w in lst] == ["mcq_002", "mcq_001"]

        # 同 id 覆盖：内容更新、条数不变
        entry1b = dict(self.ENTRY, selected="C")
        lst = tracker.upsert_quiz_wrong(SID, entry1b)
        assert len(lst) == 2
        wrong = next(w for w in lst if w["id"] == "mcq_001")
        assert wrong["selected"] == "C"

    def test_wrong_remove_and_clear(self, tracker):
        tracker.upsert_quiz_wrong(SID, dict(self.ENTRY))
        tracker.upsert_quiz_wrong(SID, dict(self.ENTRY, id="mcq_002"))

        lst = tracker.remove_quiz_wrong(SID, "mcq_001")
        assert [w["id"] for w in lst] == ["mcq_002"]

        # 隔离：清空不影响其他会话
        tracker.upsert_quiz_wrong("other", dict(self.ENTRY, id="mcq_003"))
        tracker.clear_quiz_wrong(SID)
        assert tracker.get_quiz_wrong_list(SID) == []
        assert [w["id"] for w in tracker.get_quiz_wrong_list("other")] == ["mcq_003"]

    def test_migrate_legacy_json_idempotent(self, tracker, tmp_path):
        import json as _json

        quiz_dir = tmp_path / "quiz"
        quiz_dir.mkdir()
        (quiz_dir / "done_ids.json").write_text(
            _json.dumps(["mcq_a", "mcq_b"]), encoding="utf-8"
        )
        (quiz_dir / "wrong_answers.json").write_text(
            _json.dumps([self.ENTRY]), encoding="utf-8"
        )

        tracker.migrate_legacy_quiz_json(str(quiz_dir))

        assert tracker.get_quiz_done_ids("default") == ["mcq_a", "mcq_b"]
        wrong = tracker.get_quiz_wrong_list("default")
        assert len(wrong) == 1 and wrong[0]["id"] == "mcq_001"
        assert wrong[0]["explanation"] == self.ENTRY["explanation"]
        # 源文件改名，二次执行不重复迁移
        assert (quiz_dir / "done_ids.json.migrated").exists()
        assert (quiz_dir / "wrong_answers.json.migrated").exists()
        tracker.migrate_legacy_quiz_json(str(quiz_dir))
        assert tracker.get_quiz_done_ids("default") == ["mcq_a", "mcq_b"]
        assert len(tracker.get_quiz_wrong_list("default")) == 1

    def test_migrate_legacy_json_missing_files_ok(self, tracker, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        tracker.migrate_legacy_quiz_json(str(empty))  # 不应抛错
        assert tracker.get_quiz_done_ids("default") == []
