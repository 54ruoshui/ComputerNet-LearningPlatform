"""
统一配置中心
使用 pydantic-settings 管理所有配置，必填字段缺省即报错。
"""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """GraphRAG 系统全局配置"""

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str
    neo4j_database: str = "neo4j"

    # ZhipuAI LLM
    zhipuai_api_key: str
    zhipuai_model: str = "glm-4-flash"
    zhipuai_base_url: str = "https://open.bigmodel.cn/api/paas/v4"

    # Qwen / DashScope Embeddings
    qwen_api_key: str = ""
    qwen_embedding_model: str = "text-embedding-v3"
    dashscope_api_key: str = ""

    # Retrieval
    max_entities: int = Field(default=20, ge=1, le=100)
    max_context_tokens: int = Field(default=8000, ge=500)
    similarity_threshold: float = Field(default=0.7, ge=0.0, le=1.0)

    # LLM generation
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=100)

    # Memory / conversation
    max_messages_before_summary: int = 20
    memory_backend: str = "memory"
    sqlite_db_path: str = "graphrag_checkpoints.db"

    # Web
    web_host: str = "0.0.0.0"
    web_port: int = Field(default=5001, ge=1, le=65535)
    debug: bool = False

    # Auth
    api_key: str = ""

    # Logging
    log_level: str = "INFO"
    log_format: str = "console"  # "console" | "json"

    # Fast start
    fast_start: bool = False

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            raise ValueError(f"log_level 必须是 {valid} 之一，得到: {v}")
        return upper

    @field_validator("log_format")
    @classmethod
    def validate_log_format(cls, v: str) -> str:
        valid = {"console", "json"}
        if v not in valid:
            raise ValueError(f"log_format 必须是 {valid} 之一，得到: {v}")
        return v

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例"""
    return Settings()
