"""
基于艾宾浩斯遗忘曲线的智能复习调度器

以记忆半衰期 S（stability）为唯一强度信号：
- 记忆保留率: R(t) = e^(-ln(2) * t / S)
- 复习间隔: 在预测保留率衰减到 TARGET_RETENTION 时复习
  interval = S * log2(1 / R*)
- 稳定性更新含间隔效应: 提取越困难（R 越低）的成功回忆带来的记忆增益越大
- EF (SM-2) 保留为难度调制因子
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

MIN_INTERVAL = 1       # 1 hour
MAX_INTERVAL = 365 * 24  # 1 year

# 目标保留率 R*：预测保留率降到该值时安排复习
# interval = S * log2(1/R*)，R*=0.90 → interval ≈ 0.152 × S
TARGET_RETENTION = 0.90

# 成功回忆的稳定性增益系数：S' = S × (1 + GAIN × (1-R) × q_factor × EF/2.5)
# 按时复习（R≈0.9）每次约 ×1.5
STABILITY_GAIN = 5.0
# 回忆质量 → 增益乘数（成功回忆 q>=3）
Q_FACTORS = {3: 0.8, 4: 1.0, 5: 1.2}

# 首次学习（无 elapsed 信息）时假定的复习时保留率，避免 R≈1 导致增益趋近 0
FIRST_EXPOSURE_RETENTION = 0.5

# 遗忘（quality < 3）：S' = clamp(S × LAPSE_DECAY, floor, INITIAL_HALF_LIFE)
LAPSE_DECAY = 0.25
LAPSE_FLOOR_HALF_LIFE = 4.0

# 曲线驱动的自动掌握判定阈值
MASTERY_HALF_LIFE_THRESHOLD = 504.0  # 21 天（小时）
MASTERY_MIN_REVIEWS = 3
MASTERY_MIN_QUALITY = 4


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


def interval_for_retention(
    half_life: float, target_retention: float = TARGET_RETENTION
) -> float:
    """计算保留率衰减到目标值所需时间（小时）

    R(t) = e^(-ln2·t/S) = R*  →  t = S × ln(1/R*)/ln(2)
    """
    r = min(max(target_retention, 0.01), 0.99)
    interval = half_life * math.log(1.0 / r) / math.log(2.0)
    return max(MIN_INTERVAL, min(MAX_INTERVAL, interval))


def update_ease_factor(old_ef: float, quality: int) -> float:
    """根据回忆质量更新 EF（SM-2）

    EF' = EF + (0.1 - (5-q)*(0.08 + (5-q)*0.02))
    """
    q = max(0, min(5, quality))
    delta = 0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)
    new_ef = old_ef + delta
    return max(MIN_EASE_FACTOR, new_ef)


def update_stability(
    old_half_life: float,
    quality: int,
    retention_at_review: float,
    ease_factor: float,
    is_first_exposure: bool,
) -> float:
    """根据回忆质量与复习时保留率更新记忆稳定性（间隔效应）

    - 成功回忆 (q>=3): 增益 ∝ (1-R)，提取越困难记忆强化越大；
      刚学完立即重复（R≈1）几乎无增益
    - 遗忘 (q<3): S 衰减为 1/4，钳制在 [floor, INITIAL_HALF_LIFE]
    """
    q = max(0, min(5, quality))
    if q >= 3:
        if is_first_exposure:
            r = FIRST_EXPOSURE_RETENTION
        else:
            r = min(max(retention_at_review, 0.0), 1.0)
        gain = 1.0 + STABILITY_GAIN * (1.0 - r) * Q_FACTORS[q] * (
            ease_factor / INITIAL_EASE_FACTOR
        )
        return min(old_half_life * gain, MAX_HALF_LIFE)
    else:
        return max(LAPSE_FLOOR_HALF_LIFE, min(old_half_life * LAPSE_DECAY, INITIAL_HALF_LIFE))


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
        elapsed_hours: Optional[float] = None,
        now: Optional[datetime] = None,
    ) -> dict:
        """处理一次复习事件，返回更新后的状态

        Args:
            quality: 回忆质量 0-5
            review_count: 当前复习次数
            half_life: 当前记忆半衰期（小时）
            ease_factor: 当前 EF
            elapsed_hours: 距上次复习已过时间（小时）；None 表示无历史记录
            now: 当前时间（默认 datetime.now()）

        Returns:
            {
                review_count: int,
                half_life: float (hours),
                ease_factor: float,
                next_interval: float (hours),
                next_review_at: str (ISO datetime),
                retention: float,
                retention_at_review: float,
                stability_gain: float,
            }
        """
        if now is None:
            now = datetime.now()

        is_first_exposure = review_count == 0
        if elapsed_hours is not None:
            retention_at_review = calculate_retention(half_life, elapsed_hours)
        elif review_count == 0:
            retention_at_review = FIRST_EXPOSURE_RETENTION
        else:
            # 有历史但缺 elapsed（防御路径），按目标保留率估计
            retention_at_review = TARGET_RETENTION

        new_count = review_count + 1
        new_ef = update_ease_factor(ease_factor, quality)
        new_hl = update_stability(
            half_life, quality, retention_at_review, new_ef, is_first_exposure
        )

        if quality < 3:
            # 遗忘：重置计数
            new_count = 1

        interval = interval_for_retention(new_hl)
        next_review = now + timedelta(hours=interval)

        return {
            "review_count": new_count,
            "half_life": round(new_hl, 2),
            "ease_factor": round(new_ef, 2),
            "next_interval": round(interval, 1),
            "next_review_at": next_review.isoformat(),
            "retention": 1.0,  # 刚复习完，保留率 100%
            "retention_at_review": round(retention_at_review, 4),
            "stability_gain": round(new_hl / max(half_life, 0.001), 3),
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
