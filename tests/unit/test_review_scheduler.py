"""遗忘曲线调度器单元测试 — 目标保留率驱动间隔 + 间隔效应稳定性更新"""

import math
from datetime import datetime, timedelta

import pytest

from src.services.review_scheduler import (
    FIRST_EXPOSURE_RETENTION,
    INITIAL_EASE_FACTOR,
    INITIAL_HALF_LIFE,
    MASTERY_HALF_LIFE_THRESHOLD,
    MAX_HALF_LIFE,
    MIN_EASE_FACTOR,
    ReviewScheduler,
    calculate_retention,
    interval_for_retention,
    update_ease_factor,
    update_stability,
)

LOG2_1_OVER_09 = math.log(1.0 / 0.90) / math.log(2.0)  # ≈ 0.152


class TestRetention:
    def test_zero_elapsed_full_retention(self):
        assert calculate_retention(24.0, 0.0) == 1.0

    def test_negative_elapsed_full_retention(self):
        assert calculate_retention(24.0, -5.0) == 1.0

    def test_half_life_gives_half_retention(self):
        assert calculate_retention(24.0, 24.0) == pytest.approx(0.5, abs=1e-6)

    def test_target_retention_at_scheduled_interval(self):
        interval = interval_for_retention(100.0)
        assert calculate_retention(100.0, interval) == pytest.approx(0.90, abs=1e-3)


class TestIntervalForRetention:
    def test_default_target(self):
        assert interval_for_retention(24.0) == pytest.approx(24.0 * LOG2_1_OVER_09, abs=0.01)

    def test_known_values(self):
        assert interval_for_retention(24.0) == pytest.approx(3.65, abs=0.01)
        assert interval_for_retention(1000.0) == pytest.approx(152.0, abs=0.1)

    def test_monotonic_in_half_life(self):
        assert interval_for_retention(10.0) < interval_for_retention(100.0) < interval_for_retention(1000.0)

    def test_clamped_to_min(self):
        assert interval_for_retention(0.1) == 1  # MIN_INTERVAL

    def test_clamped_to_max(self):
        assert interval_for_retention(10**9) == 365 * 24  # MAX_INTERVAL


class TestUpdateStability:
    def test_first_exposure_gain(self):
        # S' = 24 × (1 + 5×0.5×1.0×1.0) = 84
        s = update_stability(24.0, 4, FIRST_EXPOSURE_RETENTION, 2.5, is_first_exposure=True)
        assert s == pytest.approx(84.0, abs=0.01)

    def test_spacing_effect_ordering(self):
        # 提取越困难（R 越低），成功回忆的增益越大
        s_easy = update_stability(100.0, 4, 0.9, 2.5, is_first_exposure=False)
        s_hard = update_stability(100.0, 4, 0.5, 2.5, is_first_exposure=False)
        assert s_hard > s_easy
        assert s_easy == pytest.approx(150.0, abs=0.1)  # ×1.5

    def test_immediate_repeat_almost_no_gain(self):
        s = update_stability(100.0, 4, 1.0, 2.5, is_first_exposure=False)
        assert s == pytest.approx(100.0, abs=0.1)

    def test_quality_factor_ordering(self):
        s3 = update_stability(100.0, 3, 0.9, 2.5, is_first_exposure=False)
        s5 = update_stability(100.0, 5, 0.9, 2.5, is_first_exposure=False)
        assert s3 < s5

    def test_ef_modulates_gain(self):
        s_low_ef = update_stability(100.0, 4, 0.9, 1.3, is_first_exposure=False)
        s_high_ef = update_stability(100.0, 4, 0.9, 2.5, is_first_exposure=False)
        assert s_low_ef < s_high_ef

    def test_lapse_decays_and_caps(self):
        assert update_stability(1000.0, 0, 0.1, 2.5, False) == pytest.approx(24.0)  # cap
        assert update_stability(10.0, 2, 0.5, 2.5, False) == pytest.approx(4.0)  # floor
        assert update_stability(50.0, 1, 0.3, 2.5, False) == pytest.approx(12.5)  # ×0.25

    def test_success_never_exceeds_max(self):
        s = update_stability(MAX_HALF_LIFE, 5, 0.0, 10.0, False)
        assert s <= MAX_HALF_LIFE


class TestUpdateEaseFactor:
    def test_bounds(self):
        ef = 1.3
        for _ in range(10):
            ef = update_ease_factor(ef, 0)
        assert ef == MIN_EASE_FACTOR

    def test_good_quality_raises(self):
        assert update_ease_factor(2.5, 5) > 2.5


class TestProcessReview:
    def test_lapse_schedules_short_interval(self):
        """回归测试：旧算法答错后下次间隔为 144h（6 天），应显著缩短"""
        state = ReviewScheduler.process_review(
            quality=0, review_count=5, half_life=600.0, ease_factor=2.5,
            elapsed_hours=200.0, now=datetime(2026, 1, 1),
        )
        assert state["next_interval"] < 24.0
        assert state["half_life"] <= 24.0
        assert state["review_count"] == 1

    def test_first_exposure(self):
        state = ReviewScheduler.process_review(
            quality=4, review_count=0, now=datetime(2026, 1, 1),
        )
        assert state["half_life"] == pytest.approx(84.0, abs=0.01)
        assert state["review_count"] == 1
        assert state["retention_at_review"] == pytest.approx(0.5, abs=1e-3)

    def test_next_review_at_matches_interval(self):
        now = datetime(2026, 1, 1)
        state = ReviewScheduler.process_review(
            quality=4, review_count=2, half_life=100.0, ease_factor=2.5,
            elapsed_hours=15.2, now=now,
        )
        expected = now + timedelta(hours=state["next_interval"])
        actual = datetime.fromisoformat(state["next_review_at"])
        assert abs((actual - expected).total_seconds()) < 60

    def test_return_keys(self):
        state = ReviewScheduler.process_review(quality=4)
        for key in (
            "review_count", "half_life", "ease_factor", "next_interval",
            "next_review_at", "retention", "retention_at_review", "stability_gain",
        ):
            assert key in state

    def test_on_time_ladder_grows(self):
        """连续按时成功复习：S 严格递增，间隔约 ×1.5"""
        half_life = INITIAL_HALF_LIFE
        ef = INITIAL_EASE_FACTOR
        prev_interval = None
        intervals = []
        for _ in range(5):
            interval = interval_for_retention(half_life)
            state = ReviewScheduler.process_review(
                quality=4, review_count=1, half_life=half_life, ease_factor=ef,
                elapsed_hours=interval, now=datetime(2026, 1, 1),
            )
            intervals.append(state["next_interval"])
            assert state["half_life"] > half_life
            half_life = state["half_life"]
            ef = state["ease_factor"]
            prev_interval = interval
        # 每次按时复习后新间隔约为上次的 1.5 倍
        for i in range(1, len(intervals)):
            ratio = intervals[i] / intervals[i - 1]
            assert 1.3 < ratio < 1.8

    def test_mastery_threshold_reachable(self):
        """约 6-10 次按时成功复习后 S 越过掌握阈值"""
        state = {"half_life": INITIAL_HALF_LIFE, "ease_factor": INITIAL_EASE_FACTOR, "review_count": 1}
        for _ in range(12):
            interval = interval_for_retention(state["half_life"])
            new = ReviewScheduler.process_review(
                quality=4, review_count=state["review_count"],
                half_life=state["half_life"], ease_factor=state["ease_factor"],
                elapsed_hours=interval, now=datetime(2026, 1, 1),
            )
            state = new
        assert state["half_life"] >= MASTERY_HALF_LIFE_THRESHOLD
