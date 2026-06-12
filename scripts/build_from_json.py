"""
从JSON文件构建层级知识图谱

读取 data/layers/ 目录下的JSON文件，将层级结构化的网络知识导入Neo4j图数据库。

数据结构：
- 5个层级节点（物理层、数据链路层、网络层、运输层、应用层）
- 各层下的实体（Protocol、Device、Concept）
- Q&A 对（Question + Answer 节点）
- 层内关系和跨层关系

使用方法：
    python scripts/build_from_json.py                  # 导入所有层级
    python scripts/build_from_json.py --layer 3         # 只导入网络层
    python scripts/build_from_json.py --clear            # 清除现有数据后导入
    python scripts/build_from_json.py --stats            # 仅查看数据库统计
"""

import os
import sys
import json
import logging
import argparse
from pathlib import Path
from typing import List, Dict, Any

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from neo4j import GraphDatabase
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

LAYERS_DIR = project_root / "data" / "layers"

# Neo4j标签白名单
ENTITY_LABELS = {"Protocol", "Device", "Concept", "Question", "Answer"}
RELATIONSHIP_TYPES = {
    "BELONGS_TO", "DEPENDS_ON", "APPLY_TO", "OPERATES_AT",
    "WORKS_WITH", "RELATED_TO", "PROTECTS", "ATTACKS",
    "SOLVED_BY", "HAS_FUNCTION", "BETWEEN", "REFERENCES",
    "RESPONDS_TO", "ABOUT", "CONTAINS"
}


class LayerGraphBuilder:
    """从JSON构建层级知识图谱"""

    def __init__(self):
        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "")
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        logger.info("Neo4j 连接已建立")

    def close(self):
        if self.driver:
            self.driver.close()

    def clear_all(self):
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")
            logger.info("已清除所有数据")

    def create_indexes(self):
        with self.driver.session() as session:
            indexes = [
                "CREATE INDEX layer_name_idx IF NOT EXISTS FOR (n:Layer) ON (n.name)",
                "CREATE INDEX layer_number_idx IF NOT EXISTS FOR (n:Layer) ON (n.layer_number)",
                "CREATE INDEX entity_name_idx IF NOT EXISTS FOR (n:Entity) ON (n.name)",
                "CREATE INDEX entity_type_idx IF NOT EXISTS FOR (n:Entity) ON (n.entity_type)",
                "CREATE INDEX entity_layer_idx IF NOT EXISTS FOR (n:Entity) ON (n.layer)",
                "CREATE INDEX question_id_idx IF NOT EXISTS FOR (n:Question) ON (n.id)",
                "CREATE INDEX answer_id_idx IF NOT EXISTS FOR (n:Answer) ON (n.id)",
            ]
            for type_label in ["Protocol", "Device", "Concept"]:
                indexes.append(
                    f"CREATE INDEX {type_label.lower()}_name_idx IF NOT EXISTS FOR (n:{type_label}) ON (n.name)"
                )
            for idx in indexes:
                try:
                    session.run(idx)
                except Exception as e:
                    logger.warning(f"创建索引失败: {e}")
        logger.info("索引创建完成")

    def import_layer(self, layer_data: Dict[str, Any]):
        """导入单个层级的数据"""
        layer_info = layer_data["layer"]
        layer_name = layer_info["name"]
        layer_number = layer_info["layer_number"]

        logger.info(f"导入层级 {layer_number}: {layer_name}")

        with self.driver.session() as session:
            # 创建层节点
            session.run("""
                MERGE (l:Layer {name: $name})
                SET l.name_en = $name_en,
                    l.layer_number = $layer_number,
                    l.description = $description
            """, name=layer_name, name_en=layer_info.get("name_en", ""),
                 layer_number=layer_number, description=layer_info.get("description", ""))

            # 导入各类型实体
            entities = layer_data.get("entities", {})
            entity_count = 0

            for entity_type, entity_list in entities.items():
                if not isinstance(entity_list, list):
                    continue
                label = self._get_entity_label(entity_type)
                if not label:
                    continue

                for entity in entity_list:
                    name = entity.get("name", "").strip()
                    if not name:
                        continue
                    # 先用 MERGE on Entity label + name 避免跨层重复
                    session.run("""
                        MERGE (e:Entity {name: $name})
                        SET e.entity_type = $entity_type,
                            e.description = $description,
                            e.layer = $layer
                    """, name=name, entity_type=label,
                         description=entity.get("description", ""),
                         layer=layer_name)

                    # 添加子类型标签（Protocol/Device/Concept）
                    session.run(f"""
                        MATCH (e:Entity {{name: $name}})
                        SET e:{label}
                    """, name=name)

                    # 创建层→实体的CONTAINS关系
                    session.run("""
                        MATCH (l:Layer {name: $layer_name})
                        MATCH (e {name: $entity_name})
                        MERGE (l)-[:CONTAINS]->(e)
                    """, layer_name=layer_name, entity_name=name)

                    entity_count += 1

            logger.info(f"  导入 {entity_count} 个实体")

            # 导入层内关系
            relationships = layer_data.get("relationships", [])
            rel_count = 0
            for rel in relationships:
                from_name = rel.get("from", "").strip()
                to_name = rel.get("to", "").strip()
                rel_type = self._sanitize_rel_type(rel.get("type", ""))

                if not from_name or not to_name or not rel_type:
                    continue

                try:
                    session.run(f"""
                        MATCH (a {{name: $from_name}})
                        MATCH (b {{name: $to_name}})
                        MERGE (a)-[r:{rel_type}]->(b)
                        SET r.description = $description
                    """, from_name=from_name, to_name=to_name,
                         description=rel.get("description", ""))
                    rel_count += 1
                except Exception as e:
                    logger.warning(f"  关系创建失败 {from_name}-{rel_type}->{to_name}: {e}")

            logger.info(f"  导入 {rel_count} 个层内关系")

            # 导入Q&A
            questions = layer_data.get("questions", [])
            qa_count = 0
            for q in questions:
                q_id = q.get("id", f"q_{layer_number}_{qa_count}")
                question_text = q.get("question", "").strip()
                answer_text = q.get("answer", "").strip()
                if not question_text:
                    continue

                # 创建Question节点
                session.run("""
                    MERGE (q:Question {id: $q_id})
                    SET q.text = $question,
                        q.difficulty = $difficulty,
                        q.layer = $layer
                """, q_id=q_id, question=question_text,
                     difficulty=q.get("difficulty", "basic"),
                     layer=layer_name)

                # 创建Answer节点
                session.run("""
                    MERGE (a:Answer {id: $a_id})
                    SET a.text = $answer
                """, a_id=f"a_{q_id}", answer=answer_text)

                # Answer RESPONDS_TO Question
                session.run("""
                    MATCH (q:Question {id: $q_id})
                    MATCH (a:Answer {id: $a_id})
                    MERGE (a)-[:RESPONDS_TO]->(q)
                """, q_id=q_id, a_id=f"a_{q_id}")

                # Question ABOUT Layer
                session.run("""
                    MATCH (q:Question {id: $q_id})
                    MATCH (l:Layer {name: $layer_name})
                    MERGE (q)-[:ABOUT]->(l)
                """, q_id=q_id, layer_name=layer_name)

                # Question ABOUT references
                for ref_name in q.get("references", []):
                    session.run("""
                        MATCH (q:Question {id: $q_id})
                        MATCH (e {name: $ref_name})
                        MERGE (q)-[:ABOUT]->(e)
                    """, q_id=q_id, ref_name=ref_name.strip())

                qa_count += 1

            logger.info(f"  导入 {qa_count} 个Q&A对")

    def import_cross_layer_relations(self, relations: List[Dict]):
        """导入跨层关系"""
        logger.info(f"导入 {len(relations)} 个跨层关系")
        count = 0

        with self.driver.session() as session:
            for rel in relations:
                from_name = rel.get("from", "").strip()
                to_name = rel.get("to", "").strip()
                rel_type = self._sanitize_rel_type(rel.get("type", ""))

                if not from_name or not to_name or not rel_type:
                    continue

                try:
                    session.run(f"""
                        MATCH (a {{name: $from_name}})
                        MATCH (b {{name: $to_name}})
                        MERGE (a)-[r:{rel_type}]->(b)
                        SET r.description = $description,
                            r.cross_layer = true
                    """, from_name=from_name, to_name=to_name,
                         description=rel.get("description", ""))
                    count += 1
                except Exception as e:
                    logger.warning(f"跨层关系失败 {from_name}-{rel_type}->{to_name}: {e}")

        logger.info(f"成功导入 {count} 个跨层关系")

    def get_stats(self) -> Dict[str, Any]:
        stats = {}
        with self.driver.session() as session:
            # 总节点数
            result = session.run("MATCH (n) RETURN count(n) as count")
            stats["total_nodes"] = result.single()["count"]

            # 按标签统计
            result = session.run("""
                MATCH (n)
                RETURN labels(n) as labels, count(n) as count
                ORDER BY count DESC
            """)
            stats["by_label"] = {}
            for r in result:
                for label in r["labels"]:
                    if label not in stats["by_label"]:
                        stats["by_label"][label] = 0
                    stats["by_label"][label] += r["count"]

            # 关系总数
            result = session.run("MATCH ()-[r]->() RETURN count(r) as count")
            stats["total_relationships"] = result.single()["count"]

            # 按关系类型统计
            result = session.run("""
                MATCH ()-[r]->()
                RETURN type(r) as type, count(r) as count
                ORDER BY count DESC
            """)
            stats["by_rel_type"] = {r["type"]: r["count"] for r in result}

            # 各层实体数
            result = session.run("""
                MATCH (l:Layer)
                OPTIONAL MATCH (l)-[:CONTAINS]->(e:Entity)
                RETURN l.name as layer, l.layer_number as num, count(e) as entity_count
                ORDER BY num
            """)
            stats["by_layer"] = {
                r["layer"]: {"entities": r["entity_count"]}
                for r in result
            }

            # Q&A统计
            result = session.run("MATCH (q:Question) RETURN count(q) as count")
            stats["question_count"] = result.single()["count"]

            result = session.run("MATCH (a:Answer) RETURN count(a) as count")
            stats["answer_count"] = result.single()["count"]

        return stats

    @staticmethod
    def _get_entity_label(entity_type: str) -> str:
        mapping = {
            "protocols": "Protocol",
            "devices": "Device",
            "concepts": "Concept",
        }
        return mapping.get(entity_type, "")

    @staticmethod
    def _sanitize_rel_type(rel_type: str) -> str:
        import re
        cleaned = re.sub(r'[^a-zA-Z0-9_]', '_', rel_type).upper()
        return cleaned if cleaned else "RELATED_TO"


def load_layer_file(layer_number: int = None) -> List[Dict]:
    """加载层级JSON文件"""
    if layer_number is not None:
        matching = list(LAYERS_DIR.glob(f"{layer_number}_*.json"))
        if not matching:
            logger.error(f"未找到层级 {layer_number} 的文件")
            return []
        path = matching[0]
        logger.info(f"加载文件: {path.name}")
        with open(path, 'r', encoding='utf-8') as f:
            return [json.load(f)]

    all_layers = []
    for i in range(1, 6):
        matching = list(LAYERS_DIR.glob(f"{i}_*.json"))
        if matching:
            logger.info(f"加载文件: {matching[0].name}")
            with open(matching[0], 'r', encoding='utf-8') as f:
                all_layers.append(json.load(f))
        else:
            logger.warning(f"未找到层级 {i} 的文件")

    return all_layers


def load_cross_layer_file() -> List[Dict]:
    """加载跨层关系文件"""
    path = LAYERS_DIR / "cross_layer.json"
    if path.exists():
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []


def main():
    parser = argparse.ArgumentParser(description="从JSON构建层级知识图谱")
    parser.add_argument("--layer", type=int, help="只导入指定层级 (1-5)")
    parser.add_argument("--clear", action="store_true", help="导入前清除现有数据")
    parser.add_argument("--stats", action="store_true", help="仅显示数据库统计")
    args = parser.parse_args()

    builder = LayerGraphBuilder()

    try:
        if args.stats:
            stats = builder.get_stats()
            print("\n=== 知识图谱统计 ===")
            for key, value in stats.items():
                print(f"  {key}: {value}")
            return

        if args.clear:
            builder.clear_all()

        builder.create_indexes()

        # 导入层级数据
        layers = load_layer_file(args.layer)
        for layer_data in layers:
            builder.import_layer(layer_data)

        # 导入跨层关系
        if not args.layer:
            cross_rels = load_cross_layer_file()
            builder.import_cross_layer_relations(cross_rels)

        # 显示统计
        stats = builder.get_stats()
        print("\n=== 导入完成，统计信息 ===")
        print(f"  总节点数: {stats['total_nodes']}")
        print(f"  总关系数: {stats['total_relationships']}")
        print(f"  问题数: {stats['question_count']}")
        print(f"  答案数: {stats['answer_count']}")
        if stats.get("by_label"):
            print("  节点类型分布:")
            for label, count in sorted(stats["by_label"].items()):
                print(f"    {label}: {count}")

    finally:
        builder.close()


if __name__ == "__main__":
    main()
