"""
LLM 关键词提取器
使用 Qwen + with_structured_output，从用户问题中提取核心关键词。
"""

import structlog
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from src.settings import get_settings

logger = structlog.get_logger(__name__)


class KeywordsOutput(BaseModel):
    keywords: list[str] = Field(description="计算机网络核心关键词名词")


class KeywordExtractor:

    _SYSTEM_PROMPT = "从用户问题中提取最核心的计算机网络关键词名词。"

    def __init__(self, llm=None):
        if llm is not None:
            self._llm = llm
        else:
            settings = get_settings()
            api_key = settings.qwen_api_key or settings.dashscope_api_key
            self._llm = ChatOpenAI(
                base_url=settings.dashscope_base_url,
                api_key=api_key,
                model=settings.qwen_chat_model,
                temperature=0,
                max_tokens=100,
            )
        self._extractor = self._llm.with_structured_output(KeywordsOutput)

    def extract(self, question: str) -> str:
        try:
            result = self._extractor.invoke([
                {"role": "system", "content": self._SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ])
            if result.keywords:
                kw_str = " ".join(result.keywords)
                logger.info("Qwen 提取关键词", question=question, keywords=kw_str)
                return kw_str
        except Exception as e:
            logger.warning("Qwen 关键词提取失败，fallback 原始问题", error=str(e))
        return question
