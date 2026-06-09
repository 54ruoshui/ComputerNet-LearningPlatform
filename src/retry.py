"""
外部服务调用重试策略
"""

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from src.exceptions import ConnectionError_, LLMError, EmbeddingError

import logging

logger = logging.getLogger(__name__)

neo4j_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(ConnectionError_),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)

llm_retry = retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception_type(LLMError),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)

embedding_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(EmbeddingError),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
