"""
图谱可视化服务
"""

from typing import Callable

import structlog

logger = structlog.get_logger(__name__)


class GraphVisualizationService:
    """构建图谱可视化数据（节点 + 关系）"""

    def __init__(self, driver, entity_retriever_fn: Callable[[str], list[dict]]):
        self._driver = driver
        self._retrieve_entities = entity_retriever_fn

    def get_graph_data_for_visualization(self, question: str) -> dict:
        """根据问题获取图谱可视化数据"""
        entities = self._retrieve_entities(question)
        if not entities:
            return {"nodes": [], "relationships": []}
        return self.build_graph_data(entities)

    def build_graph_data(self, entities: list[dict]) -> dict:
        """基于已检索实体构建可视化数据（合并了原 _build_graph_data 和 get_graph_data_for_visualization 的重复逻辑）"""
        graph_data: dict = {"nodes": [], "relationships": []}
        try:
            core_entities = [e for e in entities if e.get("score", 0) >= 1.0]
            if not core_entities:
                core_entities = entities[:5]
            core_names = [e["name"] for e in core_entities[:10]]

            with self._driver.session() as session:
                neighbor_names = set()
                for src_name in core_names:
                    try:
                        result = session.run("""
                            MATCH (n {name: $src})-[r]-(neighbor)
                            WHERE neighbor.name IS NOT NULL
                              AND NOT neighbor.name IN $exclude
                            RETURN DISTINCT neighbor.name as name,
                                   labels(neighbor) as labels,
                                   neighbor.description as description
                            LIMIT $limit
                        """, {"src": src_name, "exclude": core_names, "limit": 3})
                        for record in result:
                            nn = record["name"]
                            if nn:
                                neighbor_names.add(nn)
                    except Exception as ex:
                        logger.debug("获取邻居失败", node=src_name, error=str(ex))

                all_names = core_names + list(neighbor_names)

                relationships = []
                if all_names:
                    result = session.run("""
                        MATCH (n1)-[r]->(n2)
                        WHERE n1.name IN $names AND n2.name IN $names
                        RETURN n1.name as start,
                               type(r) as rel_type,
                               n2.name as end
                        LIMIT 25
                    """, {"names": all_names})
                    for record in result:
                        relationships.append({
                            "start": {"name": record["start"]},
                            "end": {"name": record["end"]},
                            "type": record["rel_type"]
                        })

                connected_names = set()
                for rel in relationships:
                    connected_names.add(rel["start"]["name"])
                    connected_names.add(rel["end"]["name"])

                name_to_entity = {e["name"]: e for e in entities}
                nodes = []
                for name in all_names:
                    if name not in connected_names:
                        continue
                    if name in name_to_entity:
                        e = name_to_entity[name]
                        nodes.append({
                            "name": name,
                            "type": e.get("entity_type") or "Entity",
                            "description": e.get("description") or "",
                        })
                    else:
                        try:
                            nr = session.run("""
                                MATCH (n {name: $name})
                                RETURN COALESCE(n.entity_type, labels(n)[0]) as type,
                                       COALESCE(n.description, '') as description
                                LIMIT 1
                            """, {"name": name})
                            rec = nr.single()
                            nodes.append({
                                "name": name,
                                "type": rec["type"] if rec else "Node",
                                "description": (rec["description"] or "") if rec else "",
                            })
                        except Exception:
                            nodes.append({"name": name, "type": "Node", "description": ""})

                graph_data["nodes"] = nodes[:20]
                graph_data["relationships"] = relationships

        except Exception as e:
            logger.warning("构建可视化数据失败", error=str(e))

        return graph_data
