#!/usr/bin/env python3
"""
为 Neo4j 中已有 Entity 节点生成向量嵌入

使用方法：
    python scripts/generate_embeddings.py
    python scripts/generate_embeddings.py --batch-size 10
"""

import os
import sys
import logging
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DIMENSION = 1024


def create_vector_index(driver):
    """创建向量索引（如果不存在）"""
    with driver.session() as session:
        try:
            session.run(f"""
                CREATE VECTOR INDEX entity_embedding_index IF NOT EXISTS
                FOR (e:Entity) ON e.embedding
                OPTIONS {{
                    indexConfig: {{
                        `vector.dimensions`: {DIMENSION},
                        `vector.similarity_function`: 'cosine'
                    }}
                }}
            """)
            logger.info("向量索引 entity_embedding_index 已创建")
        except Exception as e:
            logger.warning(f"创建向量索引失败（可能已存在）: {e}")


def get_entities_without_embedding(driver):
    """获取没有 embedding 的 Entity 节点"""
    with driver.session() as session:
        result = session.run("""
            MATCH (e:Entity)
            WHERE e.embedding IS NULL
            RETURN e.name as name, e.description as description, e.entity_type as entity_type
        """)
        entities = []
        for record in result:
            entities.append({
                "name": record["name"] or "",
                "description": record["description"] or "",
                "entity_type": record["entity_type"] or "",
            })
        return entities


def count_entities(driver):
    """统计实体数量"""
    with driver.session() as session:
        total = session.run("MATCH (e:Entity) RETURN count(e) as c").single()["c"]
        with_emb = session.run("MATCH (e:Entity) WHERE e.embedding IS NOT NULL RETURN count(e) as c").single()["c"]
        return total, with_emb


def write_embeddings(driver, embeddings_data):
    """将向量写入 Neo4j"""
    updated = 0
    with driver.session() as session:
        for item in embeddings_data:
            if item["embedding"] is None:
                continue
            try:
                session.run("""
                    MATCH (e:Entity {name: $name})
                    SET e.embedding = $embedding
                """, name=item["name"], embedding=item["embedding"])
                updated += 1
            except Exception as e:
                logger.warning(f"写入 {item['name']} 失败: {e}")
    return updated


def main():
    from src.embedding_manager import EmbeddingManager

    # 初始化
    embedding_mgr = EmbeddingManager()
    if not embedding_mgr.ready:
        logger.error("Embedding 服务不可用，请检查 QWEN_API_KEY 配置")
        return

    neo4j_uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password = os.getenv("NEO4J_PASSWORD", "")
    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))

    try:
        # 统计
        total, with_emb = count_entities(driver)
        logger.info(f"Entity 总数: {total}, 已有向量: {with_emb}, 待生成: {total - with_emb}")

        if total - with_emb == 0:
            logger.info("所有实体已有向量，无需生成")
            return

        # 创建索引
        create_vector_index(driver)

        # 获取待处理实体
        entities = get_entities_without_embedding(driver)
        logger.info(f"获取到 {len(entities)} 个待处理实体")

        # 生成向量
        batch_size = 10
        all_data = []
        for i in range(0, len(entities), batch_size):
            batch = entities[i:i + batch_size]
            texts = [f"{e['name']} {e['description']}" for e in batch]
            embeddings = embedding_mgr.embed_texts(texts)

            for entity, embedding in zip(batch, embeddings):
                all_data.append({"name": entity["name"], "embedding": embedding})

            logger.info(f"进度: {min(i + batch_size, len(entities))}/{len(entities)}")

        # 写入
        updated = write_embeddings(driver, all_data)
        logger.info(f"写入完成，成功: {updated}")

        # 最终统计
        total, with_emb = count_entities(driver)
        logger.info(f"最终统计: {with_emb}/{total} 个实体已有向量")

    finally:
        driver.close()


if __name__ == "__main__":
    main()
