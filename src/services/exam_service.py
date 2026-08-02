"""
考试服务 — 试卷管理、批改、LLM薄弱点分析、针对性推荐
"""

import json
import os
import uuid
from typing import Dict, List, Optional

import structlog
from openai import OpenAI

from src.settings import get_settings

logger = structlog.get_logger(__name__)

VALID_LAYERS = {"物理层", "数据链路层", "网络层", "传输层", "应用层"}
VALID_DIFFICULTIES = {"basic", "medium", "hard"}


class ExamService:
    def __init__(self, driver, tracker=None):
        self._driver = driver
        self._tracker = tracker
        settings = get_settings()
        self._llm_client = OpenAI(
            api_key=settings.zhipuai_api_key,
            base_url=settings.zhipuai_base_url,
        )
        self._llm_model = settings.zhipuai_model

    # ==================== 试卷管理 ====================

    def get_exam_papers(self) -> dict:
        """获取所有试卷，按年份分组"""
        with self._driver.session() as session:
            rows = session.run(
                """
                MATCH (p:ExamPaper)
                OPTIONAL MATCH (p)-[:CONTAINS]->(q:MCQuestion)
                RETURN p.id AS id, p.title AS title, p.year AS year,
                       p.source AS source, p.total_score AS total_score,
                       p.description AS description,
                       count(q) AS question_count,
                       p.created_at AS created_at
                ORDER BY year DESC, created_at DESC
                """
            ).data()

        years: dict = {}
        for r in rows:
            yr = str(r.get("year", ""))
            if yr not in years:
                years[yr] = []
            years[yr].append({
                "id": r["id"],
                "title": r["title"],
                "year": r["year"],
                "source": r["source"],
                "total_score": r["total_score"],
                "description": r.get("description", ""),
                "question_count": r["question_count"],
            })

        return {"papers_by_year": years}

    def get_exam_paper(self, paper_id: str) -> Optional[dict]:
        """获取试卷详情（含全部题目，不返回正确答案）"""
        with self._driver.session() as session:
            paper = session.run(
                "MATCH (p:ExamPaper {id: $id}) RETURN p", id=paper_id
            ).single()
            if not paper:
                return None
            p = paper["p"]

            questions = session.run(
                """
                MATCH (p:ExamPaper {id: $id})-[r:CONTAINS]->(q:MCQuestion)
                RETURN q.id AS id, q.text AS text,
                       q.option_a AS option_a, q.option_b AS option_b,
                       q.option_c AS option_c, q.option_d AS option_d,
                       q.score AS score, q.question_number AS question_number,
                       q.difficulty AS difficulty, q.layer AS layer,
                       q.chapter AS chapter
                ORDER BY r.question_number
                """,
                id=paper_id,
            ).data()

        return {
            "id": p.get("id"),
            "title": p.get("title"),
            "year": p.get("year"),
            "source": p.get("source"),
            "total_score": p.get("total_score"),
            "description": p.get("description", ""),
            "questions": [
                {k: (v if v is not None else "") for k, v in q.items()}
                for q in questions
            ],
        }

    def create_exam_paper(self, data: dict) -> str:
        """从 JSON 数据创建试卷"""
        paper_id = f"exam_{uuid.uuid4().hex[:8]}"
        questions_data = data.pop("questions", [])

        with self._driver.session() as session:
            session.run(
                """
                CREATE (p:ExamPaper {
                    id: $id, title: $title, year: $year,
                    source: $source, total_score: $total_score,
                    description: $description,
                    created_at: datetime()
                })
                """,
                id=paper_id,
                title=data["title"],
                year=data.get("year", 0),
                source=data.get("source", ""),
                total_score=data.get("total_score", 0),
                description=data.get("description", ""),
            )

            for q in questions_data:
                q_id = q.get("id", f"mcq_{uuid.uuid4().hex[:8]}")
                qn = q.get("question_number", 0)
                score = q.get("score", 0)
                session.run(
                    """
                    MERGE (q:MCQuestion {id: $id})
                    SET q.text = $text,
                        q.option_a = $option_a, q.option_b = $option_b,
                        q.option_c = $option_c, q.option_d = $option_d,
                        q.correct_answer = $correct_answer,
                        q.explanation = $explanation,
                        q.difficulty = $difficulty,
                        q.layer = $layer,
                        q.chapter = $chapter,
                        q.score = $score,
                        q.question_number = $question_number
                    """,
                    id=q_id, text=q["text"],
                    option_a=q["option_a"], option_b=q["option_b"],
                    option_c=q["option_c"], option_d=q["option_d"],
                    correct_answer=q["correct_answer"], explanation=q["explanation"],
                    difficulty=q["difficulty"], layer=q["layer"],
                    chapter=q.get("chapter", ""), score=score,
                    question_number=qn,
                )

                session.run(
                    """
                    MATCH (p:ExamPaper {id: $paper_id})
                    MATCH (q:MCQuestion {id: $q_id})
                    MERGE (p)-[:CONTAINS {question_number: $qn}]->(q)
                    """,
                    paper_id=paper_id, q_id=q_id, qn=qn,
                )

                for ref in q.get("references", []):
                    session.run(
                        """
                        MATCH (q:MCQuestion {id: $id})
                        MATCH (e {name: $ref})
                        MERGE (q)-[:ABOUT]->(e)
                        """,
                        id=q_id, ref=ref.strip(),
                    )

        return paper_id

    def import_exam_paper(self, filepath: str) -> dict:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        paper_id = self.create_exam_paper(data)
        return {"paper_id": paper_id, "title": data.get("title", "")}

    # ==================== 批改 ====================

    def grade_exam(self, paper_id: str, answers: dict) -> dict:
        """批改试卷，返回评分结果"""
        with self._driver.session() as session:
            paper = session.run(
                "MATCH (p:ExamPaper {id: $id}) RETURN p", id=paper_id
            ).single()
            if not paper:
                return {"error": "试卷不存在"}

            questions = session.run(
                """
                MATCH (p:ExamPaper {id: $id})-[r:CONTAINS]->(q:MCQuestion)
                RETURN q.id AS id, q.text AS text,
                       q.correct_answer AS correct_answer,
                       q.explanation AS explanation,
                       q.score AS score, q.difficulty AS difficulty,
                       q.layer AS layer, q.chapter AS chapter
                ORDER BY r.question_number
                """,
                id=paper_id,
            ).data()

        total_score = 0
        max_score = 0
        correct_count = 0
        wrong_count = 0
        details = []

        for q in questions:
            qid = q["id"]
            score = q.get("score", 0) or 0
            max_score += score
            user_ans = answers.get(qid, "")
            is_correct = user_ans == q["correct_answer"]
            if is_correct:
                total_score += score
                correct_count += 1
            else:
                wrong_count += 1

            details.append({
                "question_id": qid,
                "text": q.get("text", ""),
                "correct": is_correct,
                "correct_answer": q["correct_answer"],
                "selected_answer": user_ans,
                "explanation": q.get("explanation", ""),
                "score": score,
                "layer": q.get("layer", ""),
                "difficulty": q.get("difficulty", ""),
                "chapter": q.get("chapter", ""),
            })

        return {
            "paper_id": paper_id,
            "total_score": total_score,
            "max_score": max_score,
            "correct_count": correct_count,
            "wrong_count": wrong_count,
            "accuracy": round(total_score / max_score * 100, 1) if max_score > 0 else 0,
            "details": details,
        }

    # ==================== 提交 & 保存 ====================

    def submit_exam(self, session_id: str, paper_id: str, answers: dict) -> dict:
        graded = self.grade_exam(paper_id, answers)
        if "error" in graded:
            return graded

        paper_title = ""
        with self._driver.session() as session:
            row = session.run(
                "MATCH (p:ExamPaper {id: $id}) RETURN p.title AS title", id=paper_id
            ).single()
            if row:
                paper_title = row["title"]

        if self._tracker:
            try:
                result_id = self._tracker.save_exam_result(
                    session_id, paper_id, paper_title, answers, graded
                )
                graded["result_id"] = result_id
            except Exception as e:
                logger.warning("保存考试结果失败", error=str(e))

        return graded

    # ==================== LLM 薄弱点分析 ====================

    def analyze_weaknesses(self, session_id: str = "", wrong_details: list = None) -> dict:
        """调用大模型分析错题薄弱点"""
        if wrong_details is None:
            if self._tracker:
                wrong_details = self._tracker.get_last_wrong_details(session_id)
            else:
                wrong_details = []

        if not wrong_details:
            return {
                "summary": "暂无错题数据",
                "weak_areas": [],
                "study_advice": "",
            }

        # 构建错题文本
        wrong_text_parts = []
        for i, d in enumerate(wrong_details, 1):
            layer_name = d.get("layer", "未知")
            diff_map = {"basic": "基础", "medium": "中等", "hard": "困难"}
            diff_name = diff_map.get(d.get("difficulty", ""), d.get("difficulty", "未知"))
            chapter = d.get("chapter", "未知")
            wrong_text_parts.append(
                f"{i}. 【{layer_name}·{diff_name}·第{chapter}章】\n"
                f"   题目：{d.get('text', '')}\n"
                f"   学生答案：{d.get('selected_answer', '')}\n"
                f"   正确答案：{d.get('correct_answer', '')}"
            )

        wrong_text = "\n\n".join(wrong_text_parts)

        prompt = f"""你是一位计算机网络教授。请分析以下学生做错的题目，找出薄弱点：

{wrong_text}

请从以下维度分析并返回 JSON 格式（不要包含 markdown 代码块标记）：
{{
  "summary": "整体评价（1-2句话）",
  "weak_areas": [
    {{"category": "layer/difficulty/chapter", "value": "具体值", "reason": "原因分析"}}
  ],
  "study_advice": "具体的学习建议（2-3句话）"
}}"""

        try:
            response = self._llm_client.chat.completions.create(
                model=self._llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1024,
            )
            content = response.choices[0].message.content.strip()
            # 清理可能的 markdown 代码块
            if content.startswith("```"):
                content = content.split("\n", 1)[1]
                if content.endswith("```"):
                    content = content[:-3]
            result = json.loads(content)
            return result
        except json.JSONDecodeError:
            return {
                "summary": "分析生成中...",
                "weak_areas": [],
                "study_advice": content if 'content' in dir() else "",
            }
        except Exception as e:
            logger.warning("LLM分析失败", error=str(e))
            return {
                "summary": f"分析暂时不可用",
                "weak_areas": [],
                "study_advice": "",
            }

    # ==================== 针对性推荐 ====================

    def get_recommendations(self, session_id: str, limit: int = 20) -> dict:
        """根据薄弱点画像推荐题目（自适应）"""
        profile = {}
        if self._tracker:
            try:
                profile = self._tracker.get_weak_point_profile(session_id)
            except Exception:
                pass

        layer_weights = self._build_layer_weights(profile.get("layer", []))
        diff_weights = self._build_difficulty_weights(profile.get("difficulty", []))
        chapter_weights = self._build_chapter_weights(profile.get("chapter", []))

        all_questions = []
        with self._driver.session() as session:
            # 获取所有非考试题的 MCQuestion
            rows = session.run(
                """
                MATCH (q:MCQuestion)
                WHERE q.correct_answer IS NOT NULL
                RETURN q.id AS id, q.text AS text,
                       q.option_a AS option_a, q.option_b AS option_b,
                       q.option_c AS option_c, q.option_d AS option_d,
                       q.difficulty AS difficulty, q.layer AS layer,
                       q.chapter AS chapter
                """
            ).data()
            all_questions = [dict(r) for r in rows]

        if not all_questions:
            return {"questions": [], "analysis": "题库为空"}

        # 排除已做题目
        try:
            from src.services.quiz_storage import get_done_ids
            done_ids = set(get_done_ids())
        except Exception:
            done_ids = set()

        available = [q for q in all_questions if q["id"] not in done_ids]
        if not available:
            available = all_questions

        # 为每道题打分
        scored: list = []
        for q in available:
            layer = q.get("layer", "")
            diff = q.get("difficulty", "")
            chapter = q.get("chapter", "")
            weight = 1.0
            weight *= layer_weights.get(layer, 1.0)
            weight *= diff_weights.get(diff, 1.0)
            weight *= chapter_weights.get(chapter, 1.0)
            scored.append((weight + (hash(q["id"]) % 100) / 100 * 0.3, q))

        scored.sort(key=lambda x: x[0], reverse=True)
        selected = scored[:limit]

        # 打乱最终顺序
        import random
        random.shuffle(selected)

        # 分析推荐构成
        layer_cnt: dict = {}
        diff_cnt: dict = {}
        for _, q in selected:
            layer_cnt[q.get("layer", "")] = layer_cnt.get(q.get("layer", ""), 0) + 1
            diff_cnt[q.get("difficulty", "")] = diff_cnt.get(q.get("difficulty", ""), 0) + 1

        return {
            "questions": [q for _, q in selected],
            "analysis": {
                "total": len(selected),
                "by_layer": layer_cnt,
                "by_difficulty": diff_cnt,
                "profile": profile,
            },
        }

    def _build_layer_weights(self, layer_errors: list) -> dict:
        """如果某层错误占比 > 40%，增大该层权重"""
        if not layer_errors:
            return {}
        total = sum(e["error_count"] for e in layer_errors)
        weights = {}
        for e in layer_errors:
            pct = e["error_count"] / total if total > 0 else 0
            if pct > 0.4:
                weights[e["value"]] = 1.0 + pct * 2
            elif pct > 0.2:
                weights[e["value"]] = 1.0 + pct
        return weights

    def _build_difficulty_weights(self, diff_errors: list) -> dict:
        if not diff_errors:
            return {}
        total = sum(e["error_count"] for e in diff_errors)
        weights = {}
        for e in diff_errors:
            pct = e["error_count"] / total if total > 0 else 0
            if pct > 0.4:
                weights[e["value"]] = 1.0 + pct * 2
            elif pct > 0.2:
                weights[e["value"]] = 1.0 + pct
        return weights

    def _build_chapter_weights(self, chapter_errors: list) -> dict:
        if not chapter_errors:
            return {}
        total = sum(e["error_count"] for e in chapter_errors)
        weights = {}
        for e in chapter_errors:
            pct = e["error_count"] / total if total > 0 else 0
            if pct > 0.2:
                weights[e["value"]] = 1.0 + pct * 2
        return weights

    # ==================== 历史 / 错误统计 ====================

    def get_exam_history(self, session_id: str) -> list:
        if self._tracker:
            return self._tracker.get_exam_history(session_id)
        return []

    def get_exam_result(self, result_id: int) -> Optional[dict]:
        if self._tracker:
            return self._tracker.get_exam_result_detail(result_id)
        return None

    def get_exam_error_stats(self, session_id: str) -> dict:
        if self._tracker:
            return self._tracker.get_exam_error_stats(session_id)
        return {"by_layer": {}, "by_difficulty": {}, "by_chapter": {}}
