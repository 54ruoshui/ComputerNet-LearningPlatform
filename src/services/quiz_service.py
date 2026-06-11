"""
选择题服务 — MCQ 的 CRUD 和查询
"""

import uuid
from typing import Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)

VALID_LAYERS = {"物理层", "数据链路层", "网络层", "传输层", "应用层"}
VALID_DIFFICULTIES = {"basic", "medium", "hard"}


class QuizService:
    def __init__(self, driver):
        self._driver = driver

    def get_questions(
        self,
        layer: Optional[str] = None,
        difficulty: Optional[str] = None,
        limit: int = 20,
    ) -> Dict:
        conditions = []
        params: Dict = {"limit": min(limit, 100)}

        if layer and layer in VALID_LAYERS:
            conditions.append("q.layer = $layer")
            params["layer"] = layer
        if difficulty and difficulty in VALID_DIFFICULTIES:
            conditions.append("q.difficulty = $difficulty")
            params["difficulty"] = difficulty

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self._driver.session() as session:
            total_result = session.run("MATCH (q:MCQuestion) RETURN count(q) AS cnt")
            total = total_result.single()["cnt"]

            result = session.run(
                f"""
                MATCH (q:MCQuestion)
                {where}
                RETURN q.id AS id, q.text AS text,
                       q.option_a AS option_a, q.option_b AS option_b,
                       q.option_c AS option_c, q.option_d AS option_d,
                       q.difficulty AS difficulty, q.layer AS layer
                ORDER BY rand()
                LIMIT $limit
                """,
                **params,
            )
            questions = [dict(record) for record in result]

        return {"questions": questions, "total": total, "filtered": len(questions)}

    def check_answer(self, question_id: str) -> Optional[Dict]:
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (q:MCQuestion {id: $id})
                RETURN q.correct_answer AS correct_answer, q.explanation AS explanation
                """,
                id=question_id,
            )
            record = result.single()
            if not record:
                return None
            return {
                "correct_answer": record["correct_answer"],
                "explanation": record["explanation"],
            }

    def get_stats(self) -> Dict:
        with self._driver.session() as session:
            total = session.run(
                "MATCH (q:MCQuestion) RETURN count(q) AS cnt"
            ).single()["cnt"]

            by_layer = {}
            for r in session.run(
                "MATCH (q:MCQuestion) RETURN q.layer AS layer, count(q) AS cnt"
            ):
                by_layer[r["layer"]] = r["cnt"]

            by_difficulty = {}
            for r in session.run(
                "MATCH (q:MCQuestion) RETURN q.difficulty AS difficulty, count(q) AS cnt"
            ):
                by_difficulty[r["difficulty"]] = r["cnt"]

        return {
            "total_questions": total,
            "by_layer": by_layer,
            "by_difficulty": by_difficulty,
        }

    def add_question(self, data: Dict) -> str:
        q_id = f"mcq_{uuid.uuid4().hex[:8]}"
        with self._driver.session() as session:
            session.run(
                """
                MERGE (q:MCQuestion {id: $id})
                SET q.text = $text,
                    q.option_a = $option_a, q.option_b = $option_b,
                    q.option_c = $option_c, q.option_d = $option_d,
                    q.correct_answer = $correct_answer,
                    q.explanation = $explanation,
                    q.difficulty = $difficulty,
                    q.layer = $layer
                """,
                id=q_id,
                text=data["text"],
                option_a=data["option_a"],
                option_b=data["option_b"],
                option_c=data["option_c"],
                option_d=data["option_d"],
                correct_answer=data["correct_answer"],
                explanation=data["explanation"],
                difficulty=data["difficulty"],
                layer=data["layer"],
            )

            session.run(
                """
                MATCH (q:MCQuestion {id: $id})
                MATCH (l:Layer {name: $layer})
                MERGE (q)-[:ABOUT]->(l)
                """,
                id=q_id,
                layer=data["layer"],
            )

            for ref in data.get("references", []):
                session.run(
                    """
                    MATCH (q:MCQuestion {id: $id})
                    MATCH (e {name: $ref})
                    MERGE (q)-[:ABOUT]->(e)
                    """,
                    id=q_id,
                    ref=ref.strip(),
                )

        return q_id

    def import_batch(self, questions: List[Dict]) -> Dict:
        imported = 0
        failed = 0
        for q in questions:
            try:
                self.add_question(q)
                imported += 1
            except Exception as e:
                logger.warning("导入题目失败", text=q.get("text", "")[:50], error=str(e))
                failed += 1
        return {"imported": imported, "failed": failed}

    def create_indexes(self):
        with self._driver.session() as session:
            for idx in [
                "CREATE INDEX mcq_id_idx IF NOT EXISTS FOR (n:MCQuestion) ON (n.id)",
                "CREATE INDEX mcq_layer_idx IF NOT EXISTS FOR (n:MCQuestion) ON (n.layer)",
                "CREATE INDEX mcq_difficulty_idx IF NOT EXISTS FOR (n:MCQuestion) ON (n.difficulty)",
            ]:
                try:
                    session.run(idx)
                except Exception as e:
                    logger.warning("创建索引失败", error=str(e))
