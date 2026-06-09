"""
掌握状态服务 — 封装掌握追踪和上下文构建
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
            return (
                f"【学生知识掌握状态】已掌握：{'、'.join(mastered_list)}。"
                f"未掌握：{'、'.join(unmastered_list)}。"
                f"请在回答时对未掌握的知识点做更详细的解释。"
            )
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
