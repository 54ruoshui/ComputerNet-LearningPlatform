"""
增强的提示工程模块
提供严格约束、领域知识库参考和证据要求的提示模板
知识库数据来源于 data/layers/*.json（5层OSI + 跨层关系）
"""

import os
import re
import json
import logging
from pathlib import Path
from typing import List, Dict, Tuple, Any, Optional
from dataclasses import dataclass, field
from neo4j import GraphDatabase
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_LAYERS_DIR = PROJECT_ROOT / "data" / "layers"


@dataclass
class PromptEngineeringConfig:
    zhipu_api_key: str = os.getenv("ZHIPUAI_API_KEY", "")
    zhipu_model: str = os.getenv("ZHIPUAI_MODEL", "glm-4-flash")
    zhipu_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    neo4j_uri: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "")

    strict_mode: bool = True
    require_evidence: bool = True
    min_confidence_threshold: float = 0.7
    max_hallucination_risk: float = 0.3

    enable_knowledge_reference: bool = True
    max_reference_items: int = 10
    reference_similarity_threshold: float = 0.8

    @property
    def neo4j_auth(self) -> Tuple[str, str]:
        return (self.neo4j_user, self.neo4j_password)


# 数据中实际使用的关系类型
RELATIONSHIP_TYPES = {
    "BELONGS_TO": "属于（如：TCP属于运输层协议）",
    "OPERATES_AT": "工作在（如：交换机工作在数据链路层）",
    "APPLY_TO": "应用于（如：CSMA/CD应用于以太网）",
    "DEPENDS_ON": "依赖于（如：HTTP依赖于TCP）",
    "RELATED_TO": "相关于（一般相关关系，谨慎使用）",
    "WORKS_WITH": "协同工作（如：路由器与交换机协同工作）",
    "EVOLVED_FROM": "演进自（如：RSTP由STP演进而来）",
    "IMPROVES_UPON": "改进于（如：4B/5B编码改进于曼彻斯特编码）",
    "CONTRASTS_WITH": "对比于（如：CSMA/CA对比CSMA/CD）",
    "IS_A_TYPE_OF": "是一种（如：Cat5e是一种双绞线）",
    "CONNECTS": "连接（如：SC连接器连接光纤）",
    "CONNECTS_TO": "连接到（如：DTE连接到DCE）",
    "COMPLEMENTS": "互补（如：MLT-3与4B/5B编码互补使用）",
    "COMPONENT_OF": "组成部分（如：传输时延是总时延的组成）",
    "SUPPORTS": "支持（如：MSTP支持不同VLAN组流量走不同路径）",
    "IMPLEMENTS": "实现（如：LACP实现链路聚合）",
    "SOLVES": "解决（如：RTS/CTS解决隐藏终端问题）",
    "REPLACES": "替代（如：HTTPS替代HTTP）",
    "EXTENDS": "扩展（如：IPv6扩展IPv4地址空间）",
    "ENABLES": "使能（如：PoE使能通过网线供电）",
    "USES": "使用（如：OSPF使用Dijkstra算法）",
    "PART_OF": "一部分（如：TCP首部是TCP报文段的一部分）",
    "INCLUDES": "包含（如：TCP拥塞控制包含慢启动阶段）",
    "DEFENDS_AGAINST": "防御（如：SYN Cookie防御SYN Flood攻击）",
    "EXPLOITS": "利用（如：XSS攻击利用网页漏洞）",
    "DESCRIBES": "描述（如：TCP首部格式描述TCP报文结构）",
    "ABOVE": "位于...之上（如：LLC子层位于MAC子层之上）",
    "CONTAINS": "包含（如：以太网帧包含源和目的MAC地址）",
    "DERIVED_FROM": "派生自（如：EIGRP派生自IGRP）",
    "AFFECTS": "影响（如：MTU影响IP分片）",
    "CONSTRAINED_BY": "受限于（如：以太网帧长受限于CSMA/CD）",
    "ALTERNATIVE_TO": "替代方案（如：UDP是TCP的替代选择）",
    "INTERACTS_WITH": "交互（如：TCP与IP交互）",
}


class EnhancedPromptEngineering:

    def __init__(self, config: PromptEngineeringConfig):
        self.config = config
        self.driver = None
        self._init_neo4j()

        self.domain_knowledge_base = self._load_domain_knowledge_base()
        self.constraint_rules = self._load_constraint_rules()
        self.evidence_templates = self._load_evidence_templates()

    def _init_neo4j(self):
        try:
            self.driver = GraphDatabase.driver(
                self.config.neo4j_uri,
                auth=self.config.neo4j_auth
            )
            self.driver.verify_connectivity()
            logger.info("Neo4j 连接成功")
        except Exception as e:
            logger.warning(f"Neo4j 连接失败: {e}")
            self.driver = None

    def close(self):
        if self.driver:
            self.driver.close()

    def _load_domain_knowledge_base(self) -> Dict[str, Any]:
        """从 data/layers/*.json 加载5层知识库"""
        kb: Dict[str, Any] = {
            "layers": {},
            "protocols": {},
            "devices": {},
            "concepts": {},
        }

        layer_files = sorted(DATA_LAYERS_DIR.glob("*.json"))
        for lf in layer_files:
            if lf.name == "cross_layer.json":
                continue
            try:
                data = json.loads(lf.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"读取 {lf} 失败: {e}")
                continue

            layer_info = data.get("layer", {})
            layer_name = layer_info.get("name", "")
            layer_number = layer_info.get("layer_number", 0)

            kb["layers"][layer_name] = {
                "osi_layer": layer_number,
                "name_en": layer_info.get("name_en", ""),
                "description": layer_info.get("description", ""),
            }

            entities = data.get("entities", {})
            for category in ("protocols", "devices", "concepts"):
                target_map = {
                    "protocols": "protocols",
                    "devices": "devices",
                    "concepts": "concepts",
                }[category]
                for ent in entities.get(category, []):
                    name = ent.get("name", "")
                    kb[target_map][name] = {
                        "layer": layer_name,
                        "layer_number": layer_number,
                        "name_en": ent.get("name_en", ""),
                        "description": ent.get("description", ""),
                    }

        return kb

    def _load_constraint_rules(self) -> Dict[str, Any]:
        return {
            "keyword_extraction": {
                "must_be_network_related": "提取的关键词必须属于计算机网络领域",
                "must_have_valid_type": "关键词类型必须是预定义的类型之一",
                "must_have_min_confidence": f"关键词置信度必须大于等于{self.config.min_confidence_threshold}",
                "must_have_evidence": "每个关键词必须有文本证据支持",
                "no_hallucination": "严禁提取文本中不存在的关键词",
                "strict_network_domain": "只提取明确的网络领域实体，拒绝任何非网络领域内容",
                "evidence_required": "必须提供具体的文本位置和上下文作为证据",
                "high_confidence_only": f"只提取置信度大于等于{self.config.min_confidence_threshold}的关键词"
            },
            "relationship_extraction": {
                "must_use_valid_type": "关系类型必须是预定义的类型之一",
                "must_be_logical": "关系必须符合逻辑，不能违反网络原理",
                "must_have_evidence": "每个关系必须有明确的文本证据支持",
                "no_circular_logic": "不允许循环逻辑的关系",
                "must_consider_direction": "关系方向必须正确",
                "both_entities_must_exist": "关系的两端实体必须都存在于文本中",
                "explicit_relationship": "只提取文本中明确描述的关系，不推断隐含关系",
                "relationship_must_be_clear": "关系描述必须清晰明确，不能模糊"
            },
            "semantic_validation": {
                "must_cross_reference": "必须与领域知识库交叉验证",
                "must_check_consistency": "必须检查内部一致性",
                "must_require_evidence": "必须有足够的证据支持",
                "must_assess_risk": "必须评估幻觉风险",
                "reject_hallucination": f"任何幻觉风险大于{self.config.max_hallucination_risk}的项目必须拒绝"
            }
        }

    def _load_evidence_templates(self) -> Dict[str, str]:
        return {
            "keyword_evidence": """
请为每个提取的关键词提供以下证据：
1. 文本位置：指出关键词在原文中的具体位置
2. 上下文：提供关键词前后的上下文（至少50个字符）
3. 支持理由：说明为什么认为这是计算机网络领域的知识点
4. 类型依据：说明为什么选择该类型
5. 置信度依据：说明置信度评分的依据
""",

            "relationship_evidence": """
请为每个提取的关系提供以下证据：
1. 文本位置：指出关系在原文中的具体位置
2. 上下文：提供关系描述前后的上下文（至少50个字符）
3. 逻辑依据：说明为什么认为这两个实体之间存在这种关系
4. 方向依据：如果是有向关系，说明为什么选择这个方向
5. 置信度依据：说明置信度评分的依据
""",

            "validation_evidence": """
请为验证结果提供以下证据：
1. 领域知识库对比：与已知领域知识的对比结果
2. 逻辑一致性检查：内部逻辑一致性的检查结果
3. 文本支持度：原文对该知识点/关系的支持程度
4. 风险评估：幻觉风险的评估结果和依据
"""
        }

    # ------------------------------------------------------------------
    # 知识库查询
    # ------------------------------------------------------------------

    def get_domain_knowledge_reference(self, term: str, item_type: str = None) -> Dict[str, Any]:
        if not self.config.enable_knowledge_reference:
            return {}

        term_lower = term.lower()
        references = []

        for category in ("protocols", "devices", "concepts", "layers"):
            for key, value in self.domain_knowledge_base.get(category, {}).items():
                if term_lower in key.lower() or key.lower() in term_lower:
                    if item_type:
                        type_map = {"protocols": "Protocol", "devices": "Device", "concepts": "Concept", "layers": "Layer"}
                        if type_map.get(category, "").lower() != item_type.lower():
                            continue

                    references.append({
                        "name": key,
                        "type": category,
                        "description": value.get("description", ""),
                        "layer": value.get("layer", ""),
                        "category": category,
                        "similarity": 1.0 if term_lower == key.lower() else 0.8
                    })

                    if len(references) >= self.config.max_reference_items:
                        break
            if len(references) >= self.config.max_reference_items:
                break

        if not references and self.driver:
            references = self._search_neo4j_knowledge(term, item_type)

        return {
            "term": term,
            "references": references,
            "has_reference": len(references) > 0,
            "confidence": max(r["similarity"] for r in references) if references else 0.0
        }

    def _search_neo4j_knowledge(self, term: str, item_type: str = None) -> List[Dict[str, Any]]:
        if not self.driver:
            return []

        try:
            with self.driver.session() as session:
                if item_type:
                    cypher = (
                        "MATCH (n) WHERE toLower(n.name) CONTAINS toLower($term) "
                        "AND ANY(label IN labels(n) WHERE label = $item_type) "
                        "RETURN n.name as name, n.description as description, labels(n) as types "
                        "LIMIT $limit"
                    )
                    params = {"term": term, "item_type": item_type, "limit": self.config.max_reference_items}
                else:
                    cypher = (
                        "MATCH (n) WHERE toLower(n.name) CONTAINS toLower($term) "
                        "RETURN n.name as name, n.description as description, labels(n) as types "
                        "LIMIT $limit"
                    )
                    params = {"term": term, "limit": self.config.max_reference_items}

                result = session.run(cypher, params)
                references = []
                for record in result:
                    name = record["name"]
                    similarity = 1.0 if name.lower() == term.lower() else 0.7
                    if similarity >= self.config.reference_similarity_threshold:
                        references.append({
                            "name": name,
                            "type": record["types"][0] if record["types"] else "Unknown",
                            "description": record.get("description", ""),
                            "category": "neo4j",
                            "similarity": similarity
                        })
                return references

        except Exception as e:
            logger.error(f"Neo4j搜索失败: {e}")
            return []

    # ------------------------------------------------------------------
    # 提示构建
    # ------------------------------------------------------------------

    def _build_entity_type_list(self) -> str:
        """根据知识库中的实际实体生成允许的类型列表"""
        lines = []
        type_labels = {"Protocol": "协议", "Device": "设备", "Concept": "概念", "Layer": "OSI层级"}

        for cat, label in type_labels.items():
            entities = self.domain_knowledge_base.get(
                {"Protocol": "protocols", "Device": "devices", "Concept": "concepts", "Layer": "layers"}[cat],
                {}
            )
            examples = list(entities.keys())[:8]
            lines.append(f"- {cat}: {label}（如：{'、'.join(examples)}）")

        lines.append("- Problem: 网络问题（如：网络不通、丢包、延迟、环路等）")
        lines.append("- Solution: 解决方案（如：网络排查、故障定位、性能优化等）")
        return "\n".join(lines)

    def _build_relationship_type_list(self) -> str:
        lines = []
        for rtype, example in RELATIONSHIP_TYPES.items():
            lines.append(f"- {rtype}: {example}")
        return "\n".join(lines)

    def build_enhanced_keyword_extraction_prompt(self, text: str) -> Dict[str, str]:
        potential_keywords = self._extract_potential_keywords(text)
        keyword_references = {}
        for kw in potential_keywords:
            keyword_references[kw] = self.get_domain_knowledge_reference(kw)

        entity_types = self._build_entity_type_list()

        system_prompt = f"""
你是一个计算机网络领域的专家，请从给定的文本中提取重要的知识点关键字。

# 严格约束（必须严格遵守）
1. 只提取明确属于计算机网络领域的实体
2. 严禁提取任何非网络领域的内容
3. 每个关键词必须在原文中有明确的文本证据
4. 置信度必须基于文本证据的强弱，不能随意给出
5. 类型必须准确，不能随意分配
6. 宁可不提取，也不要错误提取

# 允许的实体类型
{entity_types}

# 证据要求
每个关键词必须提供以下证据：
1. 文本位置：关键词在原文中的确切位置
2. 上下文：关键词前后至少50个字符的上下文
3. 支持理由：为什么认为这是网络领域的知识点
4. 类型依据：为什么选择该类型
5. 置信度依据：基于什么证据给出该置信度

# 置信度评分标准
- 0.9-1.0: 关键词在原文中明确出现且是标准网络领域术语
- 0.7-0.9: 关键词在原文中出现且明显属于网络领域
- 0.5-0.7: 关键词在原文中出现但网络领域特征不够明显
- 低于0.5: 不属于网络领域或证据不足

# 输出格式
必须返回有效的JSON数组，每个元素：
{{{{
  "name": "关键词名称（原文中的确切名称）",
  "type": "类型（Protocol/Device/Concept/Layer/Problem/Solution）",
  "confidence": 0.95,
  "description": "简要描述",
  "evidence": {{{{
    "text_position": "原文中的位置",
    "context": "上下文文本",
    "support_reason": "支持理由",
    "type_basis": "类型选择依据",
    "confidence_basis": "置信度评分依据"
  }}}}
}}}}

# 严格检查
- 只返回置信度 >= {self.config.min_confidence_threshold} 的关键词
- 如果不确定，不要提取
- 严禁猜测或推断

请返回JSON格式的结果。
"""

        user_prompt = f"""
请从以下文本中提取计算机网络知识点关键字：

文本内容：
{text}

潜在关键词参考：
{json.dumps(keyword_references, ensure_ascii=False, indent=2)}

重要提醒：
1. 只提取计算机网络领域的实体
2. 置信度必须 >= {self.config.min_confidence_threshold}
3. 提供完整的证据信息
4. 严禁提取非网络领域内容

请返回JSON格式的结果。
"""

        return {"system_prompt": system_prompt, "user_prompt": user_prompt}

    def build_enhanced_relationship_extraction_prompt(self, text: str, keywords: List[Dict[str, Any]]) -> Dict[str, str]:
        keyword_references = {}
        for kw in keywords:
            name = kw.get("name", "")
            if name:
                keyword_references[name] = self.get_domain_knowledge_reference(name, kw.get("type"))

        rel_type_list = self._build_relationship_type_list()

        system_prompt = f"""
你是一个计算机网络领域的专家，请从给定的文本和关键词中提取知识点之间的关系。

# 严格约束
1. 只提取文本中明确描述的关系
2. 关系的两端实体都必须在给定的关键词列表中
3. 关系类型必须从预定义的列表中选择
4. 必须有明确的文本证据支持
5. 关系方向必须正确，不能随意推断
6. 宁可不提取，也不要错误提取

# 允许的关系类型
{rel_type_list}

# 证据要求
每个关系必须提供以下证据：
1. 文本位置：关系描述在原文中的确切位置
2. 上下文：关系描述前后至少50个字符
3. 逻辑依据：为什么认为这两个实体之间存在这种关系
4. 方向依据：为什么选择这个方向
5. 置信度依据：基于什么证据给出该置信度

# 输出格式
必须返回有效的JSON数组，每个元素：
{{{{
  "source": {{{{"name": "源节点名称", "type": "源节点类型"}}}},
  "target": {{{{"name": "目标节点名称", "type": "目标节点类型"}}}},
  "type": "关系类型（必须是上述预定义类型之一）",
  "confidence": 0.95,
  "description": "关系描述",
  "evidence": {{{{
    "text_position": "原文中的位置",
    "context": "上下文文本",
    "logic_basis": "逻辑依据",
    "direction_basis": "方向依据",
    "confidence_basis": "置信度评分依据"
  }}}}
}}}}

# 严格检查
- 只返回置信度 >= {self.config.min_confidence_threshold} 的关系
- 源节点和目标节点都必须在关键词列表中
- 提供完整的证据信息
- 严禁推断或猜测

请返回JSON格式的结果。
"""

        user_prompt = f"""
请从以下文本和关键词中提取关系：

文本内容：
{text}

关键词：
{json.dumps(keywords, ensure_ascii=False, indent=2)}

关键词知识库参考：
{json.dumps(keyword_references, ensure_ascii=False, indent=2)}

重要提醒：
1. 只提取文本中明确描述的关系
2. 源节点和目标节点都必须在关键词列表中
3. 置信度必须 >= {self.config.min_confidence_threshold}
4. 提供完整的证据信息
5. 严禁推断隐含关系

请返回JSON格式的结果。
"""

        return {"system_prompt": system_prompt, "user_prompt": user_prompt}

    def build_enhanced_validation_prompt(self, item: Dict[str, Any], item_type: str = "knowledge") -> Dict[str, str]:
        if item_type == "knowledge":
            name = item.get("name", "")
            type_val = item.get("type", "")
            domain_reference = self.get_domain_knowledge_reference(name, type_val)
            item_content = f"知识点: {name} (类型: {type_val})"
        else:
            source_name = item.get("source", {}).get("name", "")
            target_name = item.get("target", {}).get("name", "")
            rel_type = item.get("type", "")
            domain_reference = {}
            item_content = f"关系: {source_name} --[{rel_type}]--> {target_name}"

        # 只序列化知识库的摘要（避免 prompt 过长）
        kb_summary = {}
        for cat in ("layers", "protocols", "devices", "concepts"):
            kb_summary[cat] = {
                k: {"layer": v.get("layer", ""), "description": v.get("description", "")[:80]}
                for k, v in self.domain_knowledge_base.get(cat, {}).items()
            }

        system_prompt = f"""
你是一个计算机网络领域的专家，请验证以下{item_type}的合理性和准确性。

# 领域知识库摘要
{json.dumps(kb_summary, ensure_ascii=False, indent=2)}

# 验证标准
1. 领域一致性：是否符合计算机网络领域的基本原理
2. 内部一致性：内部属性是否一致
3. 证据充分性：是否有足够的证据支持
4. 幻觉风险评估：评估幻觉风险（0-1，越高风险越大）

# 输出格式
必须返回有效的JSON格式：
- is_valid: 是否有效（布尔值）
- confidence: 置信度（0-1）
- reason: 验证原因
- suggestions: 改进建议（字符串数组）
- domain_consistency: 领域一致性评分（0-1）
- internal_consistency: 内部一致性评分（0-1）
- evidence_sufficiency: 证据充分性评分（0-1）
- hallucination_risk: 幻觉风险评估（0-1）
- domain_reference: 领域知识库参考结果

# 风险控制
- 幻觉风险高于{self.config.max_hallucination_risk}的项目应标记为无效
- 综合置信度低于{self.config.min_confidence_threshold}的项目应标记为无效

请返回JSON格式的验证结果。
"""

        user_prompt = f"""
请验证以下{item_type}的合理性：

{item_content}

描述: {item.get('description', item.get('context', ''))}

领域知识库参考：
{json.dumps(domain_reference, ensure_ascii=False, indent=2)}

请返回JSON格式的验证结果。
"""

        return {"system_prompt": system_prompt, "user_prompt": user_prompt}

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def _extract_potential_keywords(self, text: str) -> List[str]:
        """从文本中提取潜在关键词"""
        potential = set()

        # 大写缩写词（协议名等）
        potential.update(re.findall(r'\b[A-Z]{2,}\b', text))

        # 中文技术术语
        potential.update(re.findall(r'[一-龥]+(?:协议|技术|方法|机制|算法|设备|系统|层|编码|复用|交换|路由|连接|端口)', text))

        # 从知识库中匹配
        for category in ("protocols", "devices", "concepts", "layers"):
            for name in self.domain_knowledge_base.get(category, {}):
                if name in text or name.lower() in text.lower():
                    potential.add(name)

        return list(potential)

    def call_llm_with_enhanced_prompt(self, prompt_data: Dict[str, str], temperature: float = 0.3, max_tokens: int = 1500) -> Dict[str, Any]:
        try:
            headers = {
                "Authorization": f"Bearer {self.config.zhipu_api_key}",
                "Content-Type": "application/json"
            }

            data = {
                "model": self.config.zhipu_model,
                "messages": [
                    {"role": "system", "content": prompt_data["system_prompt"]},
                    {"role": "user", "content": prompt_data["user_prompt"]}
                ],
                "temperature": temperature,
                "max_tokens": max_tokens
            }

            base_url = self.config.zhipu_base_url
            if not base_url.endswith('/chat/completions'):
                base_url = base_url.rstrip('/') + '/chat/completions'

            response = requests.post(base_url, headers=headers, json=data, timeout=60)
            response.raise_for_status()

            content = response.json()["choices"][0]["message"]["content"]

            json_content = content
            if json_content.startswith('```json'):
                json_content = json_content[7:]
            elif json_content.startswith('```'):
                json_content = json_content[3:]
            if json_content.endswith('```'):
                json_content = json_content[:-3]
            json_content = json_content.strip()

            try:
                return json.loads(json_content)
            except json.JSONDecodeError:
                match = re.search(r'\{.*\}', json_content, re.DOTALL)
                if match:
                    return json.loads(match.group(0))
                raise

        except json.JSONDecodeError as e:
            logger.error(f"LLM返回的不是有效JSON: {content}")
            return {"error": "JSON解析失败", "content": content}
        except Exception as e:
            logger.error(f"LLM调用失败: {e}")
            return {"error": str(e)}


def main():
    config = PromptEngineeringConfig(
        zhipu_api_key=os.getenv("ZHIPUAI_API_KEY", ""),
        zhipu_model=os.getenv("ZHIPUAI_MODEL", "glm-4-flash"),
        strict_mode=True,
        require_evidence=True,
        min_confidence_threshold=0.7,
        max_hallucination_risk=0.3
    )

    pe = EnhancedPromptEngineering(config)

    try:
        # 打印知识库统计
        for cat in ("layers", "protocols", "devices", "concepts"):
            count = len(pe.domain_knowledge_base.get(cat, {}))
            print(f"{cat}: {count} 项")

        test_text = """
        TCP/IP协议栈是互联网的核心协议族，包括应用层、运输层、网络层和数据链路层。
        传输控制协议(TCP)是一种面向连接的、可靠的运输层协议，它通过三次握手建立连接，
        通过四次挥手断开连接。TCP提供流量控制和拥塞控制机制，确保数据可靠传输。
        路由器是网络层设备，负责在不同网络之间转发数据包。OSPF和BGP是常见的路由协议。
        以太网采用CSMA/CD介质访问控制方法，交换机根据MAC地址表转发数据帧。
        """

        print("\n=== 关键词提取 ===")
        kw_prompt = pe.build_enhanced_keyword_extraction_prompt(test_text)
        print(f"system prompt: {len(kw_prompt['system_prompt'])} chars")
        print(f"user prompt: {len(kw_prompt['user_prompt'])} chars")

        print("\n=== 关系提取 ===")
        test_keywords = [
            {"name": "TCP", "type": "Protocol", "confidence": 0.9},
            {"name": "路由器", "type": "Device", "confidence": 0.9},
            {"name": "CSMA/CD", "type": "Concept", "confidence": 0.85},
            {"name": "交换机", "type": "Device", "confidence": 0.9}
        ]
        rel_prompt = pe.build_enhanced_relationship_extraction_prompt(test_text, test_keywords)
        print(f"system prompt: {len(rel_prompt['system_prompt'])} chars")

        print("\n=== 知识库参考 ===")
        for term in ["TCP", "路由器", "CSMA/CD", "以太网", "OSPF"]:
            ref = pe.get_domain_knowledge_reference(term)
            print(f"{term}: {ref['has_reference']} (confidence={ref['confidence']})")

    finally:
        pe.close()


if __name__ == "__main__":
    main()
