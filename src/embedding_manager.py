"""
Embedding 管理器

使用 Qwen (DashScope) text-embedding-v3 生成文本向量嵌入。
"""

import os
import time
import logging
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DIMENSION = 1024


class EmbeddingManager:
    """DashScope text-embedding-v3 向量嵌入管理器"""

    def __init__(self):
        self.model_name = os.getenv("QWEN_EMBEDDING_MODEL", "text-embedding-v3")
        self.dimension = DIMENSION
        self.retry_times = 3
        self.retry_delay = 1.0

        try:
            import dashscope
            from dashscope import TextEmbedding

            api_key = os.getenv("QWEN_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
            if not api_key:
                raise ValueError("QWEN_API_KEY 或 DASHSCOPE_API_KEY 未配置")

            dashscope.api_key = api_key
            self._text_embedding = TextEmbedding
            self._ready = True
            logger.info(f"EmbeddingManager 初始化完成，模型: {self.model_name}")
        except ImportError:
            self._ready = False
            logger.warning("dashscope 未安装，语义检索不可用")
        except Exception as e:
            self._ready = False
            logger.warning(f"EmbeddingManager 初始化失败: {e}")

    @property
    def ready(self) -> bool:
        return self._ready

    def embed_query(self, text: str) -> Optional[List[float]]:
        """将用户问题转为向量"""
        if not self._ready or not text.strip():
            return None

        for attempt in range(1, self.retry_times + 1):
            try:
                response = self._text_embedding.call(
                    model=self.model_name,
                    input=text.strip()
                )
                if response.status_code == 200:
                    return response.output["embeddings"][0]["embedding"]
                logger.warning(f"Embedding API 返回 {response.status_code}，尝试 {attempt}/{self.retry_times}")
            except Exception as e:
                logger.warning(f"Embedding 生成失败（尝试 {attempt}/{self.retry_times}）: {e}")

            if attempt < self.retry_times:
                time.sleep(self.retry_delay)

        return None

    def embed_texts(self, texts: List[str]) -> List[Optional[List[float]]]:
        """批量生成向量"""
        results = []
        for text in texts:
            embedding = self.embed_query(text)
            results.append(embedding)
        return results
