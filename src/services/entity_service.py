"""
实体服务 — 实体列表查询和文本实体提取
"""

import re
from typing import List

import structlog

logger = structlog.get_logger(__name__)


class EntityService:
    """管理实体列表查询和文本中实体名称提取"""

    def __init__(self, driver):
        self._driver = driver
        self._all_entity_names: List[str] | None = None

    def get_entities_by_layer(self) -> dict:
        """按层级获取所有实体"""
        entities_by_layer: dict = {}
        total = 0
        try:
            with self._driver.session() as session:
                result = session.run("""
                    MATCH (l:Layer)-[:CONTAINS]->(e:Entity)
                    RETURN l.name AS layer, l.layer_number AS layer_num,
                           e.name AS name, e.entity_type AS entity_type, e.description AS description
                    ORDER BY l.layer_number, e.entity_type, e.name
                """)
                for record in result:
                    layer = record["layer"]
                    if layer not in entities_by_layer:
                        entities_by_layer[layer] = []
                    entities_by_layer[layer].append({
                        "name": record["name"],
                        "entity_type": record["entity_type"],
                        "description": record["description"] or "",
                    })
                    total += 1
        except Exception as e:
            logger.error("获取实体列表失败", error=str(e))
            raise
        return {"entities": entities_by_layer, "total": total}

    def get_all_entity_names(self) -> List[str]:
        """获取所有实体名称（按长度降序，用于文本匹配）"""
        if self._all_entity_names is not None:
            return self._all_entity_names
        try:
            with self._driver.session() as session:
                result = session.run("MATCH (e:Entity) WHERE e.name IS NOT NULL RETURN e.name AS name")
                self._all_entity_names = sorted([r["name"] for r in result], key=len, reverse=True)
        except Exception as e:
            logger.error("获取实体名称失败", error=str(e))
            self._all_entity_names = []
        return self._all_entity_names

    @staticmethod
    def extract_entities_from_text(text: str, entity_names: List[str]) -> List[str]:
        """从文本中提取匹配的实体名称"""
        found = []
        for name in entity_names:
            if re.search(r'(?<![a-zA-Z0-9])' + re.escape(name) + r'(?![a-zA-Z0-9])', text):
                found.append(name)
        return found
