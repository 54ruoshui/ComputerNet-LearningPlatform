"""
习题自动生成服务 — 基于知识图谱实体种子，用 LLM 生成选择题

流程：生成 → 前端预览/编辑 → 人工确认后走 quiz_service.add_question 入库（source='ai'）。
本服务自身不写库。
"""

import json
import random
import re
from typing import List, Optional

import structlog
from langchain_openai import ChatOpenAI

from src.exceptions import LLMError
from src.settings import get_settings
from src.services.entity_service import EntityService

logger = structlog.get_logger(__name__)

VALID_LAYERS = {"物理层", "数据链路层", "网络层", "传输层", "应用层"}
VALID_DIFFICULTIES = {"basic", "medium", "hard"}
MAX_COUNT = 10

# 图谱 Layer 节点用「运输层」（教材叫法），MCQ 子系统用「传输层」，两侧同义兼容
LAYER_ALIASES = {"传输层": "运输层", "运输层": "传输层"}

GENERATE_SYSTEM = (
    "你是一位计算机网络课程的命题专家，负责根据指定知识点编写高质量选择题。"
    "只输出 JSON，不要输出任何解释或 markdown 代码块标记。"
)


class QuestionGenerator:
    def __init__(self, driver, llm=None):
        self._driver = driver
        self._entity_service = EntityService(driver)
        settings = get_settings()
        self._llm = llm or ChatOpenAI(
            base_url=settings.zhipuai_base_url,
            api_key=settings.zhipuai_api_key,
            model=settings.zhipuai_model,
            temperature=0.7,
            max_tokens=4096,
        )

    def _pick_seeds(self, layer: Optional[str], seed_entities: Optional[List[str]],
                    count: int) -> List[dict]:
        """种子实体：优先用户指定（校验存在），否则按层随机抽"""
        if seed_entities:
            with self._driver.session() as session:
                rows = session.run(
                    """
                    MATCH (e:Entity) WHERE e.name IN $names
                    RETURN e.name AS name, e.description AS description, e.layer AS layer
                    """,
                    names=[n.strip() for n in seed_entities if n.strip()],
                ).data()
            found = {r["name"] for r in rows}
            missing = [n for n in seed_entities if n.strip() and n.strip() not in found]
            if missing:
                logger.warning("部分种子实体不存在，已忽略", missing=missing)
            return rows

        pool = []
        for layer_name, entities in self._entity_service.get_entities_by_layer().get("entities", {}).items():
            pool.extend({**e, "layer": layer_name} for e in entities)
        if layer:
            alias = LAYER_ALIASES.get(layer)
            pool = [e for e in pool if e.get("layer") in (layer, alias)]
        n = min(max(count + 3, 5), 8)
        return random.sample(pool, min(n, len(pool)))

    def _build_prompt(self, seeds: List[dict], layer: Optional[str], chapter: Optional[str],
                      difficulty: Optional[str], count: int) -> str:
        seed_lines = "\n".join(
            f"- {e['name']}（{e.get('layer', '')}）：{(e.get('description') or '')[:150]}"
            for e in seeds
        )
        constraints = [
            f"生成 {count} 道单项选择题，每道题必须围绕上述知识点（可组合多个知识点出题）",
            "正确选项的位置在 A/B/C/D 间随机分布",
            "干扰项要有迷惑性，贴近常见误解，但不能有两个都正确的选项",
            "解析要讲清原理，说明为什么正确、其余为什么错",
        ]
        if layer:
            constraints.append(f"每道题的 layer 字段必须填「{layer}」")
        else:
            constraints.append("layer 字段根据题目实际考察的层次填写：物理层/数据链路层/网络层/传输层/应用层")
        if chapter:
            constraints.append(f"chapter 字段填「{chapter}」")
        if difficulty:
            constraints.append(f"所有题目难度固定为 {difficulty}")

        return f"""知识点素材：
{seed_lines}

要求：
{chr(10).join(f'{i}. {c}' for i, c in enumerate(constraints, 1))}

严格按以下 JSON 数组格式输出（不要 markdown 围栏）：
[
  {{
    "text": "题干",
    "option_a": "选项 A 内容",
    "option_b": "选项 B 内容",
    "option_c": "选项 C 内容",
    "option_d": "选项 D 内容",
    "correct_answer": "A|B|C|D",
    "explanation": "答案解析",
    "difficulty": "basic|medium|hard",
    "layer": "OSI 层名",
    "chapter": "章节号，如 3.1"
  }}
]"""

    @staticmethod
    def _parse_json(content: str) -> list:
        """剥 markdown 围栏 + 容错解析 JSON 数组"""
        if not content:
            return []
        text = content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            data = json.loads(text)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            start, end = text.find("["), text.rfind("]")
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start:end + 1])
                    return data if isinstance(data, list) else []
                except json.JSONDecodeError:
                    return []
        return []

    def _validate_question(self, q: dict, layer: Optional[str]) -> Optional[dict]:
        if not isinstance(q, dict):
            return None
        required = ("text", "option_a", "option_b", "option_c", "option_d")
        if any(not str(q.get(k, "")).strip() for k in required):
            return None
        answer = str(q.get("correct_answer", "")).strip().upper()
        if answer not in ("A", "B", "C", "D"):
            return None
        options = [str(q[k]).strip() for k in required[1:]]
        if len(set(options)) != 4:
            return None
        difficulty = str(q.get("difficulty", "")).strip()
        if difficulty not in VALID_DIFFICULTIES:
            difficulty = "medium"
        q_layer = str(q.get("layer", "")).strip()
        if q_layer not in VALID_LAYERS:
            if not layer:
                return None
            q_layer = layer
        elif layer and q_layer != layer:
            q_layer = layer
        explanation = str(q.get("explanation", "")).strip()
        if not explanation:
            return None
        return {
            "text": str(q["text"]).strip()[:1000],
            "option_a": options[0][:500],
            "option_b": options[1][:500],
            "option_c": options[2][:500],
            "option_d": options[3][:500],
            "correct_answer": answer,
            "explanation": explanation[:2000],
            "difficulty": difficulty,
            "layer": q_layer,
            "chapter": str(q.get("chapter", "")).strip()[:10],
        }

    def _invoke_llm(self, prompt: str) -> str:
        message = self._llm.invoke([
            {"role": "system", "content": GENERATE_SYSTEM},
            {"role": "user", "content": prompt},
        ])
        return message.content or ""

    def generate(self, layer: Optional[str] = None, chapter: Optional[str] = None,
                 difficulty: Optional[str] = None, count: int = 5,
                 seed_entities: Optional[List[str]] = None) -> dict:
        """生成习题（不入库）。返回 questions（已通过校验并自动补全 references）"""
        count = max(1, min(count, MAX_COUNT))
        seeds = self._pick_seeds(layer, seed_entities, count)
        if not seeds:
            raise LLMError("习题生成失败", detail="知识图谱中没有可用的种子实体")

        prompt = self._build_prompt(seeds, layer, chapter, difficulty, count)
        raw = ""
        for attempt in range(2):
            try:
                raw = self._invoke_llm(prompt)
                break
            except Exception as e:
                logger.warning("习题生成 LLM 调用失败", attempt=attempt + 1, error=str(e))
                if attempt == 1:
                    raise LLMError("习题生成失败", detail=str(e))

        generated = self._parse_json(raw)
        all_names = self._entity_service.get_all_entity_names()
        valid, seen = [], set()
        for item in generated:
            q = self._validate_question(item, layer)
            if not q or q["text"] in seen:
                continue
            seen.add(q["text"])
            mentions = EntityService.extract_entities_from_text(
                f"{q['text']} {q['explanation']}", all_names)
            q["references"] = mentions[:5]
            valid.append(q)

        if not valid:
            raise LLMError("习题生成失败", detail="生成的题目均未通过校验，请调整条件重试")
        logger.info("习题生成完成", generated=len(generated), valid=len(valid))
        return {
            "questions": valid,
            "seed_entities": [e["name"] for e in seeds],
            "generated": len(generated),
            "valid": len(valid),
        }
