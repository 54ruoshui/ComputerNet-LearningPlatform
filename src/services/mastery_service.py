"""
掌握状态服务 — 封装掌握追踪和上下文构建
支持基于遗忘曲线的复习调度。
"""

from typing import List

import structlog

logger = structlog.get_logger(__name__)


class MasteryService:
    """掌握状态追踪和个性化上下文构建"""

    def __init__(self, tracker=None):
        self._tracker = tracker

    @property
    def available(self) -> bool:
        return self._tracker is not None

    def build_mastery_context(self, session_id: str) -> str:
        """构建掌握状态上下文，用于注入 Agent prompt"""
        if not self._tracker or not session_id:
            return ""
        try:
            summary = self._tracker.get_mastery_summary(session_id)
            if not summary or summary.get("total", 0) == 0:
                return ""

            mastered_list = summary.get("mastered_names", [])[:15]
            unmastered_list = summary.get("unmastered_names", [])[:15]

            context = (
                f"【学生知识掌握状态】已掌握：{'、'.join(mastered_list)}。"
                f"未掌握：{'、'.join(unmastered_list)}。"
                f"请在回答时对未掌握的知识点做更详细的解释。"
            )

            # 加入复习提醒：即将遗忘的知识点
            try:
                due = self._tracker.get_due_reviews(session_id, lookahead_hours=24)
                if due:
                    urgent = [d for d in due if d.get("is_due")][:5]
                    soon = [d for d in due if not d.get("is_due")][:5]
                    if urgent:
                        names = [d["entity_name"] for d in urgent]
                        context += (
                            f"【遗忘预警】以下知识点已到复习时间，学生可能遗忘：{'、'.join(names)}。"
                            f"请在回答时主动提及这些知识点帮助其巩固。"
                        )
                    if soon:
                        names = [d["entity_name"] for d in soon]
                        context += (
                            f"【即将复习】以下知识点临近复习期：{'、'.join(names)}。"
                        )
            except Exception:
                pass

            return context
        except Exception as e:
            logger.warning("构建掌握上下文失败", error=str(e))
            return ""

    def get_mastery(self, session_id: str) -> dict:
        mastery = self._tracker.get_mastery(session_id)
        summary = self._tracker.get_mastery_summary(session_id)
        return {"mastery": mastery, "summary": summary}

    def set_mastery(self, session_id: str, entity_name: str, mastered: bool):
        self._tracker.set_mastery(session_id, entity_name, mastered)

    def set_mastery_batch(self, session_id: str, entities: List[dict]):
        for item in entities:
            self._tracker.set_mastery(session_id, item["name"], item["mastered"])

    # ==================== 复习调度 ====================

    def record_review(
        self,
        session_id: str,
        entity_name: str,
        quality: int,
        event_type: str = "manual",
        chapter: str = "",
    ) -> dict:
        return self._tracker.record_review(
            session_id, entity_name, quality, event_type, chapter
        )

    def get_review_state(self, session_id: str) -> List[dict]:
        return self._tracker.get_review_state(session_id)

    def get_due_reviews(
        self, session_id: str, lookahead_hours: float = 48, limit: int = 50
    ) -> List[dict]:
        return self._tracker.get_due_reviews(session_id, lookahead_hours, limit)

    def get_review_stats(self, session_id: str) -> dict:
        return self._tracker.get_review_stats(session_id)

    # ==================== 答案满意度反馈 ====================

    def record_feedback(self, session_id: str, rating: int, question: str = "", comment: str = "") -> int:
        return self._tracker.record_feedback(session_id, rating, question, comment)

    def get_feedback_stats(self, session_id: str = None) -> dict:
        if not self._tracker:
            return {"total": 0, "positive": 0, "negative": 0, "satisfaction_rate": 0.0, "by_day": []}
        return self._tracker.get_feedback_stats(session_id)

    # ==================== 刷题已做/错题记录 ====================

    def get_quiz_done_ids(self, session_id: str) -> List[str]:
        return self._tracker.get_quiz_done_ids(session_id)

    def mark_quiz_done(self, session_id: str, question_id: str) -> List[str]:
        return self._tracker.mark_quiz_done(session_id, question_id)

    def get_quiz_wrong_list(self, session_id: str) -> List[dict]:
        return self._tracker.get_quiz_wrong_list(session_id)

    def upsert_quiz_wrong(self, session_id: str, entry: dict) -> List[dict]:
        return self._tracker.upsert_quiz_wrong(session_id, entry)

    def remove_quiz_wrong(self, session_id: str, question_id: str) -> List[dict]:
        return self._tracker.remove_quiz_wrong(session_id, question_id)

    def clear_quiz_wrong(self, session_id: str) -> None:
        self._tracker.clear_quiz_wrong(session_id)
