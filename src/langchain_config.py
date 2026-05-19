"""
LangGraph RAG 配置模块
管理 LangChain/LangGraph 和 Neo4j 的连接配置
"""

import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass
class LangGraphRAGConfig:
    """LangGraph RAG 系统配置"""

    # Neo4j
    neo4j_uri: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "")

    # ZhipuAI (OpenAI-compatible)
    zhipu_api_key: str = os.getenv("ZHIPUAI_API_KEY", "")
    zhipu_model: str = os.getenv("ZHIPUAI_MODEL", "glm-4-flash")
    zhipu_base_url: str = os.getenv("ZHIPUAI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")  # 不要再加 /v4，ChatOpenAI 会自动拼接

    # Retrieval
    max_entities: int = 20
    max_context_tokens: int = 8000

    # Memory / conversation
    max_messages_before_summary: int = 20
    memory_backend: str = os.getenv("MEMORY_BACKEND", "memory")  # "memory" | "sqlite"
    sqlite_db_path: str = os.getenv("SQLITE_DB_PATH", "graphrag_checkpoints.db")

    # LLM generation
    temperature: float = 0.3
    max_tokens: int = 4096
