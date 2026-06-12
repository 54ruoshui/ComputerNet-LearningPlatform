"""
实体检索服务
语义搜索 + 关键词搜索 + 上下文构建 + 邻居/子图查询
"""

import structlog

from src.exceptions import ConnectionError_
from src.keyword_extractor import KeywordExtractor
from src.embedding_manager import EmbeddingManager

logger = structlog.get_logger(__name__)


class EntityRetriever:
    """基于 Neo4j 的实体检索（语义 + 关键词 + 图查询）"""

    def __init__(self, driver, embedding_mgr: EmbeddingManager,
                 keyword_extractor: KeywordExtractor,
                 max_entities: int = 20,
                 viz_service=None):
        self._driver = driver
        self._embedding_mgr = embedding_mgr
        self._keyword_extractor = keyword_extractor
        self.max_entities = max_entities
        self._viz_service = viz_service

    # ==================== 主检索 ====================

    def search(self, question: str) -> str:
        """检索入口：提取实体 → 获取QA和层级 → 构建上下文"""
        entities = self.retrieve_entities(question)
        if not entities:
            return "未找到相关实体信息。"

        entity_names = [e["name"] for e in entities]
        qa_list = self.get_qa_for_entities(entity_names)
        layers = self.get_layers_for_entities(entity_names)

        return self.build_context(entities, qa_list, layers)

    def search_with_graph(self, question: str) -> dict:
        """一次检索同时返回文本上下文和可视化图数据"""
        entities = self.retrieve_entities(question)
        if not entities:
            return {
                "context": "未在知识图谱中找到相关内容。",
                "graph_data": {"nodes": [], "relationships": []},
            }

        entity_names = [e["name"] for e in entities]
        qa_list = self.get_qa_for_entities(entity_names)
        layers = self.get_layers_for_entities(entity_names)
        context = self.build_context(entities, qa_list, layers)

        if self._viz_service:
            graph_data = self._viz_service.build_graph_data(entities)
        else:
            graph_data = {"nodes": [], "relationships": []}

        return {"context": context, "graph_data": graph_data}

    # ==================== 实体检索 ====================

    def retrieve_entities(self, question: str) -> list[dict]:
        """向量语义检索"""
        entities = self.semantic_search(question, top_k=self.max_entities)
        entities.sort(key=lambda e: e["score"], reverse=True)
        return entities[:self.max_entities]

    def semantic_search(self, question: str, top_k: int = 10) -> list[dict]:
        """向量语义检索：先提取关键词再向量化"""
        if not self._embedding_mgr.ready:
            return []

        query_text = self._keyword_extractor.extract(question)
        query_embedding = self._embedding_mgr.embed_query(query_text)
        if not query_embedding:
            return []

        entities = []
        try:
            with self._driver.session() as session:
                # 优先使用新版 db.index.vector.search API（Neo4j 2026.01+）
                # 若不可用则 fallback 到 queryNodes（Neo4j 5.x）
                try:
                    result = session.run("""
                        CALL db.index.vector.search('entity_embedding_index', $top_k, $embedding)
                        YIELD node, score
                        RETURN node.name as name,
                               node.entity_type as entity_type,
                               node.description as description,
                               node.layer as layer,
                               score
                    """, top_k=top_k, embedding=query_embedding)
                except Exception:
                    result = session.run("""
                        CALL db.index.vector.queryNodes('entity_embedding_index', $top_k, $embedding)
                        YIELD node, score
                        RETURN node.name as name,
                               node.entity_type as entity_type,
                               node.description as description,
                               node.layer as layer,
                               score
                    """, top_k=top_k, embedding=query_embedding)
                for record in result:
                    if record["name"]:
                        entities.append({
                            "name": record["name"],
                            "entity_type": record["entity_type"] or "Unknown",
                            "description": record["description"] or "",
                            "layer": record["layer"] or "",
                            "score": record["score"] * 5
                        })
        except Exception as e:
            logger.warning("语义检索失败", error=str(e))

        return entities

    # ==================== 辅助查询 ====================

    def get_qa_for_entities(self, entity_names: list[str]) -> list[dict]:
        qa_list = []
        try:
            with self._driver.session() as session:
                cypher = """
                MATCH (q:Question)-[:ABOUT]->(e)
                WHERE e.name IN $names
                OPTIONAL MATCH (a:Answer)-[:RESPONDS_TO]->(q)
                RETURN q.text as question, q.difficulty as difficulty,
                       a.text as answer, q.layer as layer
                """
                result = session.run(cypher, {"names": entity_names})
                for record in result:
                    qa_list.append({
                        "question": record["question"] or "",
                        "answer": record["answer"] or "",
                        "difficulty": record["difficulty"] or "",
                        "layer": record["layer"] or ""
                    })
        except Exception as e:
            logger.warning("获取QA失败", error=str(e))
        return qa_list

    def get_layers_for_entities(self, entity_names: list[str]) -> list[dict]:
        layers = []
        seen = set()
        try:
            with self._driver.session() as session:
                cypher = """
                MATCH (l:Layer)-[:CONTAINS]->(e)
                WHERE e.name IN $names
                RETURN l.name as name, l.layer_number as num, l.description as description
                ORDER BY num
                """
                result = session.run(cypher, {"names": entity_names})
                for record in result:
                    lname = record["name"]
                    if lname not in seen:
                        layers.append({
                            "name": lname,
                            "number": record["num"],
                            "description": record["description"] or ""
                        })
                        seen.add(lname)
        except Exception as e:
            logger.warning("获取层级信息失败", error=str(e))
        return layers

    def get_relationships_between(self, names: list[str]) -> list[dict]:
        rels = []
        if not names:
            return rels
        try:
            with self._driver.session() as session:
                cypher = """
                MATCH (a)-[r]->(b)
                WHERE a.name IN $names AND b.name IN $names
                RETURN a.name as start, type(r) as rel_type, b.name as end,
                       r.description as description
                LIMIT 20
                """
                result = session.run(cypher, {"names": names})
                for record in result:
                    rels.append({
                        "start": record["start"],
                        "type": record["rel_type"],
                        "end": record["end"],
                        "description": record["description"] or ""
                    })
        except Exception as e:
            logger.warning("获取关系失败", error=str(e))
        return rels

    # ==================== 上下文构建 ====================

    def build_context(self, entities: list[dict], qa_list: list[dict], layers: list[dict]) -> str:
        parts = []

        if layers:
            parts.append("## 相关层级\n")
            for layer in layers:
                parts.append(f"**{layer['name']}**（第{layer['number']}层）")
                if layer.get("description"):
                    parts.append(f"  {layer['description'][:200]}")

        parts.append("\n## 相关实体\n")
        for entity in entities[:10]:
            layer_tag = f" [{entity['layer']}]" if entity.get("layer") else ""
            parts.append(f"- **{entity['name']}** ({entity['entity_type']}){layer_tag}")
            if entity.get("description"):
                parts.append(f"  {entity['description'][:200]}")

        entity_names = [e["name"] for e in entities[:10]]
        relationships = self.get_relationships_between(entity_names)
        if relationships:
            parts.append("\n## 实体间关系\n")
            for rel in relationships[:10]:
                parts.append(f"- {rel['start']} --[{rel['type']}]--> {rel['end']}")
                if rel.get("description"):
                    parts.append(f"  {rel['description'][:150]}")

        if qa_list:
            parts.append("\n## 相关问答\n")
            for qa in qa_list[:5]:
                parts.append(f"**Q: {qa['question']}**")
                if qa.get("answer"):
                    parts.append(f"A: {qa['answer'][:500]}")

        return "\n".join(parts)

    # ==================== Web API 辅助 ====================

    def keyword_search(self, query: str, limit: int = 10) -> list[dict]:
        """节点搜索：提取关键词后通过向量语义检索匹配节点"""
        if not query.strip():
            return []
        return self.semantic_search(query, top_k=limit)

    def get_neighbors(self, node_name: str, depth: int = 2) -> list[dict]:
        if depth == 1:
            cypher = """
            MATCH (start {name: $node_name})-[r]-(neighbor)
            RETURN DISTINCT neighbor, labels(neighbor) as types, 1 as distance
            ORDER BY labels(neighbor)[0] LIMIT 50
            """
        elif depth == 2:
            cypher = """
            MATCH path = (start {name: $node_name})-[*1..2]-(neighbor)
            RETURN DISTINCT neighbor, labels(neighbor) as types, length(path) as distance
            ORDER BY distance, labels(neighbor)[0] LIMIT 50
            """
        else:
            cypher = f"""
            MATCH path = (start {{name: $node_name}})-[*1..{depth}]-(neighbor)
            RETURN DISTINCT neighbor, labels(neighbor) as types, length(path) as distance
            ORDER BY distance, labels(neighbor)[0] LIMIT 50
            """
        try:
            with self._driver.session() as session:
                result = session.run(cypher, {"node_name": node_name})
                neighbors = []
                for record in result:
                    neighbor_data = dict(record["neighbor"])
                    types = record["types"]
                    if isinstance(types, list) and types:
                        neighbor_data["type"] = types[0]
                    else:
                        neighbor_data["type"] = str(types) if types else "Unknown"
                    neighbor_data["distance"] = record["distance"]
                    neighbors.append(neighbor_data)
                return neighbors
        except Exception as e:
            logger.warning("获取节点邻居失败", node=node_name, error=str(e))
            raise ConnectionError_(
                "获取节点邻居失败",
                detail=f"node={node_name}",
                cause=e,
            ) from e

    def get_subgraph_by_query(self, query: str, limit: int = 20) -> dict:
        nodes = self.keyword_search(query, limit)
        if not nodes:
            return {"nodes": [], "relationships": []}

        node_names = [n.get("name") for n in nodes if n.get("name")]
        try:
            with self._driver.session() as session:
                result = session.run("""
                MATCH (n1)-[r]-(n2)
                WHERE n1.name IN $node_names AND n2.name IN $node_names
                RETURN n1, type(r) as rel_type, n2
                """, {"node_names": node_names})
                relationships = []
                for record in result:
                    relationships.append({
                        "type": record["rel_type"],
                        "start": dict(record["n1"]),
                        "end": dict(record["n2"]),
                    })
                return {"nodes": nodes, "relationships": relationships}
        except Exception as e:
            logger.warning("获取子图失败", error=str(e))
            return {"nodes": nodes, "relationships": []}
