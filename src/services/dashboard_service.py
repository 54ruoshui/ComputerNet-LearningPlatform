"""
仪表盘服务 — 聚合掌握度、复习、考试、薄弱点、活动数据 + LLM 学习报告
"""

import structlog
from openai import OpenAI

from src.settings import get_settings
from src.services.entity_service import EntityService

logger = structlog.get_logger(__name__)


class DashboardService:
    def __init__(self, driver, tracker=None, quiz_service=None):
        self._driver = driver
        self._tracker = tracker
        self._quiz_service = quiz_service
        self._entity_service = EntityService(driver)

    # ==================== 数据聚合 ====================

    def get_overview(self, session_id: str) -> dict:
        return {
            "mastery": self._get_mastery_overview(session_id),
            "review": self._get_review_stats(session_id),
            "exam_trend": self._get_exam_trend(session_id),
            "weak_points": self._get_weak_points(session_id),
            "activity": self._get_activity(session_id),
            "sections": self._get_section_heatmap(session_id),
        }

    def _get_mastery_overview(self, session_id: str) -> dict:
        layers_raw = []
        try:
            layers_raw = self._entity_service.get_entities_by_layer().get("entities", {})
        except Exception as e:
            logger.warning("获取实体分层失败", error=str(e))

        if not self._tracker:
            return {"total": 0, "mastered": 0, "learning": 0, "untouched": 0, "layers": []}

        mastery_map = self._tracker.get_mastery(session_id)
        learning = self._tracker.get_learning_entities(session_id)

        # 按 layer_number 排序的分层信息
        layers = []
        total_mastered = 0
        total_learning = 0
        total_all = 0
        for layer_name, entities in layers_raw.items():
            names = [e["name"] for e in entities]
            layer_mastered = sum(1 for n in names if mastery_map.get(n))
            layer_learning = sum(1 for n in names if n in learning and not mastery_map.get(n))
            layer_untouched = len(names) - layer_mastered - layer_learning
            total_mastered += layer_mastered
            total_learning += layer_learning
            total_all += len(names)
            layers.append({
                "layer": layer_name,
                "total": len(names),
                "mastered": layer_mastered,
                "learning": layer_learning,
                "untouched": layer_untouched,
                "rate": round(layer_mastered / len(names), 4) if names else 0,
            })

        # 实体可能不属于任何 Layer（无 CONTAINS 边），补齐差异
        all_known = {n for e in layers_raw.values() for n in [x["name"] for x in e]}
        for name, mastered in mastery_map.items():
            if name not in all_known:
                total_all += 1
                if mastered:
                    total_mastered += 1
        for name in learning:
            if name not in all_known and not mastery_map.get(name):
                total_all += 1
                total_learning += 1

        return {
            "total": total_all,
            "mastered": total_mastered,
            "learning": total_learning,
            "untouched": max(total_all - total_mastered - total_learning, 0),
            "layers": layers,
        }

    def _get_review_stats(self, session_id: str) -> dict:
        if not self._tracker:
            return {}
        return self._tracker.get_review_stats(session_id)

    def _get_exam_trend(self, session_id: str) -> list:
        if not self._tracker:
            return []
        history = self._tracker.get_exam_history(session_id)
        trend = []
        for item in reversed(history[-10:]):
            trend.append({
                "date": (item.get("completed_at") or "")[:10],
                "paper_title": item.get("paper_title", ""),
                "accuracy": item.get("accuracy", 0),
                "score": item.get("total_score", 0),
                "max_score": item.get("max_score", 0),
            })
        return trend

    def _get_weak_points(self, session_id: str) -> list:
        if not self._tracker:
            return []
        profile = self._tracker.get_weak_point_profile(session_id)
        merged = []
        for category in ("layer", "difficulty", "chapter"):
            for item in profile.get(category, []):
                merged.append({
                    "category": category,
                    "value": item["value"],
                    "error_count": item["error_count"],
                    "percentage": item["percentage"],
                })
        merged.sort(key=lambda x: x["error_count"], reverse=True)
        return merged[:10]

    def _get_activity(self, session_id: str) -> list:
        activity = []
        if self._tracker:
            for ev in self._tracker.get_recent_review_events(session_id, limit=20):
                quality = ev.get("quality")
                quality_suffix = f"（质量{quality}）" if quality is not None else ""
                activity.append({
                    "type": "review",
                    "time": ev.get("recorded_at", ""),
                    "detail": f"{ev.get('event_type', 'manual')} · {ev.get('entity_name', '')}{quality_suffix}",
                })
            for exam in self._tracker.get_exam_history(session_id)[:10]:
                activity.append({
                    "type": "exam",
                    "time": exam.get("completed_at", ""),
                    "detail": f"{exam.get('paper_title', '')} · 得分 {exam.get('total_score', 0)}/{exam.get('max_score', 0)}（正确率 {exam.get('accuracy', 0)}%）",
                })

        activity.sort(key=lambda x: x["time"], reverse=True)
        return activity[:20]

    def _get_section_heatmap(self, session_id: str) -> list:
        sections = []
        if self._quiz_service:
            for chapter in self._quiz_service.get_chapter_structure():
                for section in chapter.get("sections", []):
                    sections.append({
                        "chapter": section["chapter"],
                        "section": section["section"],
                        "total": 0,
                        "correct": 0,
                        "rate": None,
                    })

        accuracy = {}
        if self._tracker:
            accuracy = self._tracker.get_chapter_accuracy(session_id)

        for sec in sections:
            stats = accuracy.get(sec["chapter"])
            if stats and stats["total"] > 0:
                sec["total"] = stats["total"]
                sec["correct"] = stats["correct"]
                sec["rate"] = round(stats["correct"] / stats["total"] * 100, 1)
        return sections

    # ==================== LLM 学习报告 ====================

    def generate_report(self, session_id: str) -> str | None:
        overview = self.get_overview(session_id)
        prompt = self._build_report_prompt(session_id, overview)
        settings = get_settings()
        try:
            client = OpenAI(
                api_key=settings.zhipuai_api_key,
                base_url=settings.zhipuai_base_url,
            )
            response = client.chat.completions.create(
                model=settings.zhipuai_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=2048,
            )
            content = response.choices[0].message.content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1]
                if content.endswith("```"):
                    content = content[:-3]
            return content.strip()
        except Exception as e:
            logger.warning("LLM 学习报告生成失败", error=str(e))
            return None

    def _build_report_prompt(self, session_id: str, overview: dict) -> str:
        mastery = overview["mastery"]
        review = overview["review"]
        layers_text = "、".join(
            f"{l['layer']} {l['rate'] * 100:.0f}%"
            for l in mastery.get("layers", [])
        ) or "暂无"
        trend_text = "、".join(
            f"{t['date']} {t['paper_title']} 正确率{t['accuracy']}%"
            for t in overview["exam_trend"]
        ) or "暂无考试记录"
        weak_text = "、".join(
            f"[{w['category']}]{w['value']}(错{w['error_count']}次)"
            for w in overview["weak_points"][:5]
        ) or "暂无"

        return f"""你是一位计算机网络课程的学习导师。以下是学生（{session_id}）的学习数据统计，请生成一份中文学习报告（Markdown 格式），包含以下小节：
# 学习概况
# 各层掌握情况
# 复习情况
# 考试趋势
# 薄弱环节
# 学习建议

学习数据：
- 知识掌握：总实体 {mastery['total']}，已掌握 {mastery['mastered']}，学习中 {mastery['learning']}，未接触 {mastery['untouched']}
- 各层掌握率：{layers_text}
- 复习：累计复习 {review.get('total_reviews', 0)} 次，近7天 {review.get('reviews_7d', 0)} 次，待复习 {review.get('due_now', 0)} 个，平均保留率 {review.get('avg_retention', 0)}
- 考试正确率趋势：{trend_text}
- 薄弱点 Top5：{weak_text}

要求：语言简洁专业，学习建议具体可执行，全文控制在 500 字以内。"""