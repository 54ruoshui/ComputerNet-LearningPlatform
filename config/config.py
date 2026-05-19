"""
GraphRAG系统配置文件
"""

import os
from dataclasses import dataclass
from typing import Tuple

@dataclass
class GraphRAGConfig:
    """GraphRAG系统配置"""
    # Neo4j配置
    neo4j_uri: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_auth: Tuple[str, str] = (
        os.getenv("NEO4J_USER", "neo4j"),
        os.getenv("NEO4J_PASSWORD", "aixi1314")
    )
    neo4j_database: str = os.getenv("NEO4J_DATABASE", "neo4j")
    
    # 智谱AI配置
    zhipu_api_key: str = os.getenv("ZHIPUAI_API_KEY", "")
    zhipu_model: str = os.getenv("ZHIPUAI_MODEL", "glm-4-flash")
    zhipu_base_url: str = os.getenv("ZHIPUAI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    
    # 检索配置
    max_context_length: int = int(os.getenv("MAX_CONTEXT_LENGTH", "4000"))
    max_retrieved_nodes: int = int(os.getenv("MAX_RETRIEVED_NODES", "20"))
    similarity_threshold: float = float(os.getenv("SIMILARITY_THRESHOLD", "0.7"))
    search_depth: int = int(os.getenv("SEARCH_DEPTH", "2"))
    
    # Web配置
    flask_host: str = os.getenv("FLASK_HOST", "0.0.0.0")
    flask_port: int = int(os.getenv("FLASK_PORT", "5000"))
    flask_debug: bool = os.getenv("FLASK_DEBUG", "False").lower() == "true"
    
    # 缓存配置
    enable_cache: bool = os.getenv("ENABLE_CACHE", "True").lower() == "true"
    cache_ttl: int = int(os.getenv("CACHE_TTL", "3600"))  # 缓存过期时间（秒）
    
    # 日志配置
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_file: str = os.getenv("LOG_FILE", "graph_rag.log")

# 预定义的查询模板
QUERY_TEMPLATES = {
    "protocol_comparison": "比较{protocol1}和{protocol2}的区别和特点",
    "device_function": "解释{device}的工作原理和功能",
    "concept_explanation": "什么是{concept}，请详细解释",
    "troubleshooting": "如何解决{problem}，请提供解决方案",
    "layer_function": "{layer}的主要功能是什么",
    "security_mechanism": "解释{security}安全机制的工作原理",
    "network_type": "{network_type}的特点和应用场景",
    "protocol_flow": "描述{protocol}的工作流程",
    "configuration_guide": "如何配置{technology}",
    "best_practices": "{technology}的最佳实践是什么"
}

# 示例问题库
EXAMPLE_QUESTIONS = [
    "TCP和UDP有什么区别？",
    "HTTP和HTTPS的关系是什么？",
    "路由器的工作原理是什么？",
    "如何解决网络环路问题？",
    "什么是VLAN，它有什么作用？",
    "三次握手和四次挥手的过程是什么？",
    "DNS解析的步骤有哪些？",
    "什么是子网掩码，如何计算？",
    "OSPF和BGP的区别是什么？",
    "防火墙有哪些类型？",
    "什么是NAT，它的工作原理是什么？",
    "VPN有哪些类型，各有什么特点？",
    "负载均衡器的作用是什么？",
    "什么是SDN，它与传统网络有什么区别？",
    "IPv6相比IPv4有哪些优势？"
]

# 节点类型颜色映射
NODE_TYPE_COLORS = {
    'Protocol': '#ff6b6b',
    'Device': '#4ecdc4',
    'Layer': '#45b7d1',
    'Knowledge': '#96ceb4',
    'SecurityConcept': '#feca57',
    'NetworkType': '#ff9ff3',
    'Model': '#54a0ff',
    'Function': '#48dbfb',
    'Topology': '#1dd1a1',
    'Problem': '#ee5a24',
    'Solution': '#10ac84'
}

# 支持的文件格式
SUPPORTED_EXPORT_FORMATS = ['json', 'csv', 'graphml', 'gexf']

# API限流配置
RATE_LIMIT_CONFIG = {
    "default": "100/hour",
    "query": "10/minute",
    "search": "30/minute"
}
