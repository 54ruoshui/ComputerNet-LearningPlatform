"""
Embedding 管理器
使用 Qwen (DashScope) text-embedding-v3 生成文本向量嵌入。
"""

import logging

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log

from src.exceptions import EmbeddingError
from src.settings import get_settings

logger = structlog.get_logger(__name__)

DIMENSION = 1024


class EmbeddingManager:
    """DashScope text-embedding-v3 向量嵌入管理器"""

    def __init__(self):
        settings = get_settings()
        self.model_name = settings.qwen_embedding_model
        self.dimension = DIMENSION

        try:
            import dashscope
            from dashscope import TextEmbedding

            api_key = settings.qwen_api_key or settings.dashscope_api_key
            if not api_key:
                raise EmbeddingError(
                    "QWEN_API_KEY 或 DASHSCOPE_API_KEY 未配置",
                    detail="请在 .env 中设置至少一个 embedding API key",
                )

            dashscope.api_key = api_key
            self._text_embedding = TextEmbedding
            self._ready = True
            logger.info("EmbeddingManager 初始化完成", model=self.model_name)
        except ImportError:
            self._ready = False
            logger.warning("dashscope 未安装，语义检索不可用")
        except EmbeddingError:
            raise
        except Exception as e:
            self._ready = False
            raise EmbeddingError(
                "EmbeddingManager 初始化失败",
                detail=str(e),
                cause=e,
            ) from e

    @property
    def ready(self) -> bool:
        return self._ready

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(EmbeddingError),
        before_sleep=before_sleep_log(logging.getLogger(__name__), logging.WARNING),
        reraise=True,
    )
    def _call_embedding_api(self, text: str):
        """调用 Embedding API（带重试）"""
        response = self._text_embedding.call(
            model=self.model_name,
            input=text,
        )
        if response.status_code == 200:
            return response.output["embeddings"][0]["embedding"]
        raise EmbeddingError(
            f"Embedding API 返回 {response.status_code}",
            detail=f"model={self.model_name}, status={response.status_code}",
        )

    def embed_query(self, text: str) -> list[float] | None:
        """将用户问题转为向量"""
        if not self._ready or not text.strip():
            return None

        try:
            return self._call_embedding_api(text.strip())
        except EmbeddingError as e:
            logger.warning("Embedding 生成失败", error=str(e))
            return None
        except Exception as e:
            logger.warning("Embedding 生成异常", error=str(e))
            return None

    def embed_texts(self, texts: list[str]) -> list[list[float] | None]:
        """批量生成向量"""
        return [self.embed_query(text) for text in texts]
