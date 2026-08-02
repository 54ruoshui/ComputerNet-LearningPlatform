"""
基于艾宾浩斯遗忘曲线的智能复习调度器

使用 SM-2 (SuperMemo 2) 算法：
- 记忆保留率: R(t) = e^(-ln(2) * t / half_life)
- 复习间隔: I(n) = I(n-1) * EF
- EF 根据回忆质量动态调整
"""

import math
from datetime import datetime, timedelta
from typing import Optional

# 初始半衰期（小时）— 首次学习后约 1 天遗忘 50%
INITIAL_HALF_LIFE = 24.0  # 1 day
MIN_HALF_LIFE = 1.0
MAX_HALF_LIFE = 8760.0 * 2  # ~2 years

INITIAL_EASE_FACTOR = 2.5
MIN_EASE_FACTOR = 1.3

# 首次复习间隔（小时）
FIRST_INTERVAL = 24    # 1 day
SECOND_INTERVAL = 144  # 6 days
MIN_INTERVAL = 1       # 1 hour
MAX_INTERVAL = 365 * 24  # 1 year


def calculate_retention(half_life: float, elapsed_hours: float) -> float:
    """计算当前记忆保留率 R(t) = e^(-ln(2) * t / S)

    Args:
        half_life: 记忆半衰期（小时）
        elapsed_hours: 距上次复习已过时间（小时）

    Returns:
        保留率 0.0~1.0
    """
    if elapsed_hours <= 0:
        return 1.0
    return math.exp(-math.log(2) * elapsed_hours / max(half_life, 0.001))


def calculate_next_interval(review_count: int, ease_factor: float) -> float:
    """根据复习次数和 EF 计算下次复习间隔（小时）

    - 第 1 次复习: 1 天
    - 第 2 次复习: 6 天
    - 第 n 次 (n>2): 上一次间隔 * EF
    """
    if review_count <= 0:
        return FIRST_INTERVAL
    elif review_count == 1:
        return SECOND_INTERVAL
    else:
        # SM-2: I(n) = I(n-1) * EF
        # 这里 review_count>=2 时用递增的 EF 乘法
        interval = SECOND_INTERVAL
        for _ in range(1, review_count):
            interval *= ease_factor
        return min(interval, MAX_INTERVAL)


def update_ease_factor(old_ef: float, quality: int) -> float:
    """根据回忆质量更新 EF

    EF' = EF + (0.1 - (5-q)*(0.08 + (5-q)*0.02))
    """
    q = max(0, min(5, quality))
    delta = 0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)
    new_ef = old_ef + delta
    return max(MIN_EASE_FACTOR, new_ef)


def update_half_life(old_half_life: float, quality: int, ease_factor: float) -> float:
    """根据回忆质量更新记忆半衰期

    - quality >= 3: half_life *= (1 + ease_factor * quality / 5)
    - quality < 3: half_life 重置为初始值
    """
    q = max(0, min(5, quality))
    if q >= 3:
        new_hl = old_half_life * (1.0 + ease_factor * q / 5.0)
        return min(new_hl, MAX_HALF_LIFE)
    else:
        # 遗忘严重，重置
        return INITIAL_HALF_LIFE


def get_urgency(retention: float, hours_until_due: float) -> float:
    """计算复习紧急度（0-100）

    紧急度高 = 保留率低 + 已过期时间长
    """
    retention_score = (1.0 - retention) * 70
    overdue_score = 0.0
    if hours_until_due < 0:
        # 已过期，过期越久越急（最多 +30）
        overdue_score = min(30, abs(hours_until_due) / 24 * 10)
    return min(100, retention_score + overdue_score)


def get_retention_label(retention: float) -> str:
    """保留率 → 标签"""
    if retention >= 0.85:
        return "牢固"
    elif retention >= 0.70:
        return "正常"
    elif retention >= 0.50:
        return "薄弱"
    else:
        return "需复习"


def get_retention_color(retention: float) -> str:
    """保留率 → 颜色标记"""
    if retention >= 0.85:
        return "green"
    elif retention >= 0.70:
        return "blue"
    elif retention >= 0.50:
        return "orange"
    else:
        return "red"


class ReviewScheduler:
    """复习调度器 — 纯算法层，不依赖数据库"""

    @staticmethod
    def process_review(
        quality: int,
        review_count: int = 0,
        half_life: float = INITIAL_HALF_LIFE,
        ease_factor: float = INITIAL_EASE_FACTOR,
        now: Optional[datetime] = None,
    ) -> dict:
        """处理一次复习事件，返回更新后的状态

        Args:
            quality: 回忆质量 0-5
            review_count: 当前复习次数
            half_life: 当前记忆半衰期（小时）
            ease_factor: 当前 EF
            now: 当前时间（默认 datetime.now()）

        Returns:
            {
                review_count: int,
                half_life: float (hours),
                ease_factor: float,
                next_interval: float (hours),
                next_review_at: str (ISO datetime),
                retention: float,
            }
        """
        if now is None:
            now = datetime.now()

        new_count = review_count + 1
        new_ef = update_ease_factor(ease_factor, quality)
        new_hl = update_half_life(half_life, quality, new_ef)

        if quality < 3:
            # 遗忘：重置计数
            new_count = 1

        interval = calculate_next_interval(new_count, new_ef)
        next_review = now + timedelta(hours=interval)

        return {
            "review_count": new_count,
            "half_life": round(new_hl, 2),
            "ease_factor": round(new_ef, 2),
            "next_interval": round(interval, 1),
            "next_review_at": next_review.isoformat(),
            "retention": 1.0,  # 刚复习完，保留率 100%
        }

    @staticmethod
    def get_current_state(
        half_life: float,
        last_review_at: Optional[str],
        next_review_at: Optional[str],
        review_count: int = 0,
        now: Optional[datetime] = None,
    ) -> dict:
        """获取当前复习状态（用于展示）

        Returns:
            {
                retention: float,
                hours_since_review: float,
                hours_until_due: float,
                is_due: bool,
                urgency: float,
                label: str,
                color: str,
            }
        """
        if now is None:
            now = datetime.now()

        if last_review_at:
            last_review = datetime.fromisoformat(last_review_at)
            elapsed = (now - last_review).total_seconds() / 3600
        else:
            elapsed = 0

        retention = calculate_retention(half_life, elapsed)

        if next_review_at:
            next_review = datetime.fromisoformat(next_review_at)
            hours_until = (next_review - now).total_seconds() / 3600
        else:
            hours_until = 0

        is_due = hours_until <= 0
        urgency = get_urgency(retention, hours_until)

        return {
            "retention": round(retention, 4),
            "hours_since_review": round(elapsed, 1),
            "hours_until_due": round(hours_until, 1),
            "is_due": is_due,
            "urgency": round(urgency, 1),
            "label": get_retention_label(retention),
            "color": get_retention_color(retention),
        }
