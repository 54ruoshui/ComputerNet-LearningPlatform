"""
批量导入选择题到 Neo4j

使用方法：
    python scripts/import_mcq.py                        # 导入 data/mcq_questions.json
    python scripts/import_mcq.py --file custom.json      # 指定文件
    python scripts/import_mcq.py --stats                 # 仅查看统计
"""

import json
import sys
import logging
import argparse
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from neo4j import GraphDatabase
from dotenv import load_dotenv
import os

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

VALID_LAYERS = {"物理层", "数据链路层", "网络层", "传输层", "应用层"}
VALID_DIFFICULTIES = {"basic", "medium", "hard"}


class MCQImporter:
    def __init__(self):
        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "")
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        logger.info("Neo4j 连接已建立")

    def close(self):
        self.driver.close()

    def create_indexes(self):
        with self.driver.session() as session:
            for idx in [
                "CREATE INDEX mcq_id_idx IF NOT EXISTS FOR (n:MCQuestion) ON (n.id)",
                "CREATE INDEX mcq_layer_idx IF NOT EXISTS FOR (n:MCQuestion) ON (n.layer)",
                "CREATE INDEX mcq_difficulty_idx IF NOT EXISTS FOR (n:MCQuestion) ON (n.difficulty)",
            ]:
                try:
                    session.run(idx)
                except Exception as e:
                    logger.warning(f"创建索引失败: {e}")

    def validate(self, q: dict) -> list[str]:
        errors = []
        for field in ["text", "option_a", "option_b", "option_c", "option_d", "correct_answer", "explanation"]:
            if not q.get(field, "").strip():
                errors.append(f"缺少必填字段: {field}")
        if q.get("correct_answer") not in ("A", "B", "C", "D"):
            errors.append("correct_answer 必须为 A/B/C/D")
        if q.get("difficulty") not in VALID_DIFFICULTIES:
            errors.append(f"difficulty 必须为 {VALID_DIFFICULTIES}")
        if q.get("layer") not in VALID_LAYERS:
            errors.append(f"layer 必须为 {VALID_LAYERS}")
        return errors

    def import_file(self, filepath: str):
        with open(filepath, "r", encoding="utf-8") as f:
            questions = json.load(f)
        if not isinstance(questions, list):
            logger.error("JSON 文件格式错误：根元素必须是数组")
            return

        imported = 0
        skipped = 0
        with self.driver.session() as session:
            for i, q in enumerate(questions):
                errors = self.validate(q)
                if errors:
                    logger.warning(f"题目 #{i+1} 验证失败: {', '.join(errors)}")
                    skipped += 1
                    continue

                q_id = q.get("id", f"mcq_import_{i+1}")
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
                    id=q_id, text=q["text"],
                    option_a=q["option_a"], option_b=q["option_b"],
                    option_c=q["option_c"], option_d=q["option_d"],
                    correct_answer=q["correct_answer"], explanation=q["explanation"],
                    difficulty=q["difficulty"], layer=q["layer"],
                )

                session.run(
                    """
                    MATCH (q:MCQuestion {id: $id})
                    MATCH (l:Layer {name: $layer})
                    MERGE (q)-[:ABOUT]->(l)
                    """,
                    id=q_id, layer=q["layer"],
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
                imported += 1

        logger.info(f"导入完成: {imported} 成功, {skipped} 跳过")

    def show_stats(self):
        with self.driver.session() as session:
            total = session.run("MATCH (q:MCQuestion) RETURN count(q) AS c").single()["c"]
            by_layer = {r["layer"]: r["c"] for r in session.run("MATCH (q:MCQuestion) RETURN q.layer AS layer, count(q) AS c")}
            by_diff = {r["d"]: r["c"] for r in session.run("MATCH (q:MCQuestion) RETURN q.difficulty AS d, count(q) AS c")}

        print("\n=== 选择题统计 ===")
        print(f"  总数: {total}")
        print(f"  按层次: {by_layer}")
        print(f"  按难度: {by_diff}")


def main():
    parser = argparse.ArgumentParser(description="批量导入选择题")
    parser.add_argument("--file", default=str(project_root / "data" / "mcq_questions.json"), help="JSON 文件路径")
    parser.add_argument("--stats", action="store_true", help="仅显示统计")
    args = parser.parse_args()

    importer = MCQImporter()
    try:
        if args.stats:
            importer.show_stats()
        else:
            importer.create_indexes()
            importer.import_file(args.file)
            importer.show_stats()
    finally:
        importer.close()


if __name__ == "__main__":
    main()
