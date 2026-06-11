"""
LangGraph RAG 配置模块
保留 LangGraphRAGConfig 类名向后兼容，内部委托给 Settings。
"""

from src.settings import get_settings


class LangGraphRAGConfig:
    """LangGraph RAG 系统配置（向后兼容包装）"""

    def __init__(self):
        s = get_settings()
        self.neo4j_uri = s.neo4j_uri
        self.neo4j_user = s.neo4j_user
        self.neo4j_password = s.neo4j_password
        self.zhipuai_api_key = s.zhipuai_api_key
        self.zhipu_model = s.zhipuai_model
        self.zhipu_base_url = s.zhipuai_base_url
        self.max_entities = s.max_entities
        self.max_context_tokens = s.max_context_tokens
        self.max_messages_before_summary = s.max_messages_before_summary
        self.memory_backend = s.memory_backend
        self.sqlite_db_path = s.sqlite_db_path
        self.temperature = s.temperature
        self.max_tokens = s.max_tokens
