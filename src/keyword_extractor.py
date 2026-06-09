"""
LLM 关键词提取器
从用户问题中提取核心关键词，用于改进语义检索。
不使用 with_structured_output（与 glm-4-flash 不兼容），改用 prompt + 解析。
"""

import json
import re

import structlog
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from src.settings import get_settings

logger = structlog.get_logger(__name__)


class KeywordsOutput(BaseModel):
    """关键词提取的结构化输出"""
    keywords: list[str] = Field(description="从问题中提取的3-5个计算机网络核心关键词")


class KeywordExtractor:
    """基于 LLM 的关键词提取器"""

    _SYSTEM_PROMPT = """你是一个计算机网络领域的关键词提取专家。
从用户的问题中提取3-5个最核心的计算机网络关键词。
只返回一个 JSON 数组，不要返回其他任何内容。
示例格式：["TCP", "三次握手", "传输层"]"""

    def __init__(self):
        settings = get_settings()
        self._llm = ChatOpenAI(
            base_url=settings.zhipuai_base_url,
            api_key=settings.zhipuai_api_key,
            model=settings.zhipuai_model,
            temperature=0,
            max_tokens=100,
        )

    def extract(self, question: str) -> str:
        """从问题中提取关键词，返回空格分隔的字符串。失败时返回原始问题。"""
        try:
            response = self._llm.invoke([
                {"role": "system", "content": self._SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ])
            keywords = self._parse_keywords(response.content)
            if keywords:
                result = " ".join(keywords)
                logger.info("LLM 提取关键词", question=question, keywords=result)
                return result
        except Exception as e:
            logger.warning("LLM 关键词提取失败，使用原始问题", error=str(e))
        return question

    def _parse_keywords(self, text: str) -> list[str]:
        """从 LLM 回复中解析关键词列表"""
        text = text.strip()

        # 尝试直接 JSON 解析
        try:
            result = json.loads(text)
            if isinstance(result, list):
                return [str(k).strip() for k in result if str(k).strip()]
        except (json.JSONDecodeError, TypeError):
            pass

        # 尝试提取 JSON 数组
        match = re.search(r'\[.*?\]', text, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
                if isinstance(result, list):
                    return [str(k).strip() for k in result if str(k).strip()]
            except (json.JSONDecodeError, TypeError):
                pass

        # 尝试逗号/顿号/换行分割
        parts = re.split(r'[,，、\n]+', text)
        cleaned = [p.strip().strip('"\'""''') for p in parts if p.strip()]
        if cleaned:
            return cleaned

        return []
