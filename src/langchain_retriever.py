"""
LangChain Neo4j 自定义检索器（层级知识图谱版本）

将 Neo4j Cypher 查询封装为 LangChain BaseRetriever，返回 Document 对象。
支持关键词匹配 + 向量语义检索的混合检索策略。
"""

import re
import json
import logging
from typing import List, Dict, Any, Optional

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from neo4j import GraphDatabase

from src.embedding_manager import EmbeddingManager

logger = logging.getLogger(__name__)


class Neo4jGraphRetriever(BaseRetriever):
    """基于 Neo4j 的层级知识图谱检索器"""

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    max_entities: int = 20
    max_context_tokens: int = 8000

    _driver: Any = None

    class Config:
        arbitrary_types_allowed = True

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self._driver is None:
            self._driver = GraphDatabase.driver(
                self.neo4j_uri,
                auth=(self.neo4j_user, self.neo4j_password)
            )
            self._self_created_driver = True
        else:
            self._self_created_driver = False
        self._embedding_mgr = EmbeddingManager()

    def close(self):
        if getattr(self, '_self_created_driver', False) and self._driver:
            self._driver.close()

    # ==================== BaseRetriever 接口 ====================

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun,
    ) -> List[Document]:
        context = self._search(query)
        return [Document(page_content=context, metadata={"source": "knowledge_graph"})]

    # ==================== 检索模式 ====================

    def _search(self, question: str) -> str:
        """搜索：匹配关键词 → 获取实体和Q&A → 构建上下文"""
        entities = self._retrieve_entities(question)
        if not entities:
            return "未找到相关实体信息。"

        entity_names = [e["name"] for e in entities]
        qa_list = self._get_qa_for_entities(entity_names)
        layers = self._get_layers_for_entities(entity_names)

        return self._build_context(entities, qa_list, layers)

    # ==================== 实体检索 ====================

    def _retrieve_entities(self, question: str) -> List[Dict]:
        """混合检索：语义向量检索 + 关键词匹配"""
        entities = []
        seen_names = set()

        # 语义检索
        semantic_results = self._semantic_search(question, top_k=10)
        for item in semantic_results:
            name = item["name"]
            if name and name not in seen_names:
                entities.append(item)
                seen_names.add(name)

        # 关键词检索补充
        keyword_results = self._keyword_search(question)
        for item in keyword_results:
            name = item["name"]
            if name and name not in seen_names:
                entities.append(item)
                seen_names.add(name)

        entities.sort(key=lambda e: e["score"], reverse=True)
        return entities[:self.max_entities]

    def _semantic_search(self, question: str, top_k: int = 10) -> List[Dict]:
        """向量语义检索"""
        if not self._embedding_mgr.ready:
            return []

        query_embedding = self._embedding_mgr.embed_query(question)
        if not query_embedding:
            return []

        entities = []
        try:
            with self._driver.session() as session:
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
                        # 将 cosine 相似度 (0~1) 映射到与关键词匹配可比的分数范围 (1~5)
                        entities.append({
                            "name": record["name"],
                            "entity_type": record["entity_type"] or "Unknown",
                            "description": record["description"] or "",
                            "layer": record["layer"] or "",
                            "score": record["score"] * 5
                        })
        except Exception as e:
            logger.warning(f"语义检索失败: {e}")

        return entities

    def _keyword_search(self, question: str) -> List[Dict]:
        """关键词匹配检索"""
        entities = []
        keywords = self._extract_keywords(question)
        seen_names = set()

        with self._driver.session() as session:
            for keyword in keywords[:5]:
                cypher = """
                MATCH (e:Entity)
                WHERE e.name = $keyword
                   OR e.name CONTAINS $keyword
                   OR e.description CONTAINS $keyword
                RETURN e.name as name,
                       e.entity_type as entity_type,
                       e.description as description,
                       e.layer as layer,
                       CASE
                           WHEN e.name = $keyword THEN 3
                           WHEN e.name CONTAINS $keyword THEN 2
                           ELSE 1
                       END as score
                ORDER BY score DESC
                LIMIT $limit
                """
                result = session.run(cypher, {
                    "keyword": keyword,
                    "limit": self.max_entities // max(len(keywords[:5]), 1) + 1
                })
                for record in result:
                    name = record["name"]
                    if not name or name in seen_names:
                        if name in seen_names:
                            for existing in entities:
                                if existing["name"] == name:
                                    existing["score"] = max(existing["score"], record["score"])
                        continue
                    entities.append({
                        "name": name,
                        "entity_type": record["entity_type"] or "Unknown",
                        "description": record["description"] or "",
                        "layer": record["layer"] or "",
                        "score": record["score"]
                    })
                    seen_names.add(name)

        return entities

    def _get_qa_for_entities(self, entity_names: List[str]) -> List[Dict]:
        """获取与指定实体相关的Q&A"""
        qa_list = []
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
        return qa_list

    def _get_layers_for_entities(self, entity_names: List[str]) -> List[Dict]:
        """获取实体所属的层级信息"""
        layers = []
        seen = set()
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
        return layers

    # ==================== 上下文构建 ====================

    def _build_context(self, entities: List[Dict], qa_list: List[Dict], layers: List[Dict]) -> str:
        parts = []

        # 层级概述
        if layers:
            parts.append("## 相关层级\n")
            for layer in layers:
                parts.append(f"**{layer['name']}**（第{layer['number']}层）")
                if layer.get("description"):
                    parts.append(f"  {layer['description'][:200]}")

        # 实体信息
        parts.append("\n## 相关实体\n")
        for entity in entities[:10]:
            layer_tag = f" [{entity['layer']}]" if entity.get("layer") else ""
            parts.append(f"- **{entity['name']}** ({entity['entity_type']}){layer_tag}")
            if entity.get("description"):
                parts.append(f"  {entity['description'][:200]}")

        # 相关实体间关系
        entity_names = [e["name"] for e in entities[:10]]
        relationships = self._get_relationships_between(entity_names)
        if relationships:
            parts.append("\n## 实体间关系\n")
            for rel in relationships[:10]:
                parts.append(f"- {rel['start']} --[{rel['type']}]--> {rel['end']}")
                if rel.get("description"):
                    parts.append(f"  {rel['description'][:150]}")

        # Q&A
        if qa_list:
            parts.append("\n## 相关问答\n")
            for qa in qa_list[:5]:
                parts.append(f"**Q: {qa['question']}**")
                if qa.get("answer"):
                    parts.append(f"A: {qa['answer'][:500]}")

        return "\n".join(parts)

    def _get_relationships_between(self, names: List[str]) -> List[Dict]:
        """获取指定节点集之间的关系"""
        rels = []
        if not names:
            return rels
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
        return rels

    # ==================== 检索 + 可视化（合并接口） ====================

    def search_with_graph(self, question: str) -> Dict[str, Any]:
        """一次检索同时返回文本上下文和可视化图数据，避免重复调用 _retrieve_entities"""
        entities = self._retrieve_entities(question)
        if not entities:
            return {
                "context": "未在知识图谱中找到相关内容。",
                "graph_data": {"nodes": [], "relationships": []},
            }

        entity_names = [e["name"] for e in entities]

        # 文本上下文
        qa_list = self._get_qa_for_entities(entity_names)
        layers = self._get_layers_for_entities(entity_names)
        context = self._build_context(entities, qa_list, layers)

        # 可视化数据 — 基于已检索的实体扩展邻居
        graph_data = self._build_graph_data(entities)

        return {"context": context, "graph_data": graph_data}

    def _build_graph_data(self, entities: List[Dict]) -> Dict[str, Any]:
        """基于已检索实体构建可视化数据"""
        graph_data: Dict[str, Any] = {"nodes": [], "relationships": []}
        try:
            core_entities = [e for e in entities if e.get("score", 1) >= 2]
            if not core_entities:
                core_entities = entities[:5]
            core_names = [e["name"] for e in core_entities[:10]]

            with self._driver.session() as session:
                # 扩展邻居
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
                        logger.debug(f"获取 {src_name} 邻居失败: {ex}")

                all_names = core_names + list(neighbor_names)

                # 获取关系
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

                # 只保留有连接的节点
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
            logger.warning(f"构建可视化数据失败: {e}")

        return graph_data

    # ==================== 可视化辅助 ====================

    def get_graph_data_for_visualization(self, question: str) -> Dict[str, Any]:
        """根据问题获取图谱可视化数据"""
        graph_data = {"nodes": [], "relationships": []}
        try:
            entities = self._retrieve_entities(question)
            if not entities:
                return graph_data

            with self._driver.session() as session:
                core_entities = [e for e in entities if e.get("score", 1) >= 2]
                if not core_entities:
                    core_entities = entities[:5]

                core_names = [e["name"] for e in core_entities[:10]]

                # 扩展邻居
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
                        logger.debug(f"获取 {src_name} 邻居失败: {ex}")

                all_names = core_names + list(neighbor_names)

                # 获取关系
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

                # 只保留有连接的节点
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
            logger.warning(f"获取图谱数据失败: {e}")

        return graph_data

    # ==================== 图谱统计 ====================

    def get_graph_stats(self) -> Dict[str, Any]:
        stats = {}
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

            # 层级统计
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

            # Q&A统计
            try:
                result = session.run("MATCH (q:Question) RETURN count(q) as count")
                stats["question_count"] = result.single()["count"]
            except Exception:
                stats["question_count"] = 0

            stats["totalNodes"] = stats["entity_count"]
            stats["node_count"] = stats["entity_count"]

        return stats

    # ==================== Web API 辅助查询 ====================

    def keyword_search(self, query: str, limit: int = 10) -> List[Dict]:
        safe_query = re.sub(r'[^a-zA-Z0-9一-龥]', '', query)
        if not safe_query:
            return []

        cypher = """
        MATCH (n)
        WHERE (n.name IS NOT NULL AND n.name =~ $query_regex)
           OR (n.description IS NOT NULL AND n.description =~ $query_regex)
        RETURN n, labels(n) as types
        LIMIT $limit
        """
        query_regex = f"(?i).*{safe_query}.*"
        with self._driver.session() as session:
            result = session.run(cypher, {"query_regex": query_regex, "limit": limit})
            nodes = []
            for record in result:
                node_data = dict(record["n"])
                types = record["types"]
                if isinstance(types, list) and types:
                    node_data["type"] = types[0]
                else:
                    node_data["type"] = str(types) if types else "Unknown"
                nodes.append(node_data)
            return nodes

    def get_neighbors(self, node_name: str, depth: int = 2) -> List[Dict]:
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

    def get_subgraph_by_query(self, query: str, limit: int = 20) -> Dict:
        nodes = self.keyword_search(query, limit)
        if not nodes:
            return {"nodes": [], "relationships": []}

        node_names = [n.get("name") for n in nodes if n.get("name")]
        cypher = """
        MATCH (n1)-[r]-(n2)
        WHERE n1.name IN $node_names AND n2.name IN $node_names
        RETURN n1, type(r) as rel_type, n2
        """
        with self._driver.session() as session:
            result = session.run(cypher, {"node_names": node_names})
            relationships = []
            for record in result:
                relationships.append({
                    "type": record["rel_type"],
                    "start": dict(record["n1"]),
                    "end": dict(record["n2"]),
                })
            return {"nodes": nodes, "relationships": relationships}

    # ==================== 工具方法 ====================

    @staticmethod
    def _extract_keywords(question: str) -> List[str]:
        stop_words = {'的', '是', '在', '有', '和', '与', '或', '但', '如果', '那么',
                     '因为', '所以', '什么', '如何', '为什么', '请', '了', '吗', '呢'}
        words = re.findall(r'[a-zA-Z]+|[一-龥]+', question)
        keywords = []
        for word in words:
            if word.lower() not in stop_words and len(word) > 1:
                keywords.append(word)

        network_terms = ['tcp', 'udp', 'http', 'https', 'vlan', 'ip', 'dns', 'nat',
                        'ospf', 'bgp', 'stp', 'vpn', '路由', '交换', '防火墙',
                        'arp', 'icmp', 'dhcp', 'rip', 'ipsec', 'quic', 'sctp',
                        'igmp', 'isis', 'eigrp', 'vrrp', 'rstp', 'mstp', 'lacp',
                        'mqtt', 'snmp', 'ntp', 'ftp', 'smtp', 'ssh', 'tls',
                        'websocket', 'ppp', 'wifi', 'poe', 'cidr', 'ttl', 'mss']
        for term in network_terms:
            if term in question.lower() and term not in keywords:
                keywords.append(term)
        return keywords
