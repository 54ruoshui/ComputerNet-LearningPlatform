"""
GraphRAG 自定义异常体系
"""


class GraphRAGError(Exception):
    """GraphRAG 系统基础异常"""

    def __init__(self, message: str, detail: str = "", cause: Exception | None = None):
        self.message = message
        self.detail = detail
        self.cause = cause
        super().__init__(message)


class ConfigError(GraphRAGError):
    """配置缺失或无效"""


class ConnectionError_(GraphRAGError):
    """外部服务连接失败（Neo4j 等）"""


class RetrievalError(GraphRAGError):
    """知识检索失败"""


class LLMError(GraphRAGError):
    """LLM 调用失败"""


class EmbeddingError(GraphRAGError):
    """向量嵌入生成失败"""
