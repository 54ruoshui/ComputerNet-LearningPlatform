"""
图谱统计服务
"""

import structlog

logger = structlog.get_logger(__name__)


class GraphStatsService:
    """Neo4j 图谱统计查询"""

    def __init__(self, driver):
        self._driver = driver

    def get_stats(self) -> dict:
        stats = {}
        try:
            with self._driver.session() as session:
                try:
                    result = session.run("MATCH (n) RETURN count(n) as count")
                    stats["entity_count"] = result.single()["count"]
                except Exception:
                    stats["entity_count"] = 0

                try:
                    result = session.run("""
                        MATCH (n)
                        WHERE n.name IS NOT NULL
                        RETURN labels(n)[0] as type, count(n) as count
                        ORDER BY count DESC LIMIT 20
                    """)
                    stats["nodes_by_type"] = {r["type"]: r["count"] for r in result if r["type"]}
                except Exception:
                    stats["nodes_by_type"] = {}

                try:
                    result = session.run("MATCH ()-[r]->() RETURN count(r) as count")
                    stats["relationship_count"] = result.single()["count"]
                except Exception:
                    stats["relationship_count"] = 0

                try:
                    result = session.run("""
                        MATCH (l:Layer)
                        OPTIONAL MATCH (l)-[:CONTAINS]->(e:Entity)
                        RETURN l.name as layer, l.layer_number as num,
                               count(e) as entity_count
                        ORDER BY num
                    """)
                    stats["layers"] = {}
                    for r in result:
                        stats["layers"][r["layer"]] = r["entity_count"]
                except Exception:
                    stats["layers"] = {}

                try:
                    result = session.run("MATCH (q:Question) RETURN count(q) as count")
                    stats["question_count"] = result.single()["count"]
                except Exception:
                    stats["question_count"] = 0

                stats["totalNodes"] = stats["entity_count"]
                stats["node_count"] = stats["entity_count"]
        except Exception as e:
            logger.warning("获取图谱统计失败", error=str(e))

        return stats
