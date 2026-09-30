"""QuestionGenerator 单元测试 — mock LLM 的生成/解析/校验/references 补全"""

from unittest.mock import MagicMock

import pytest

from src.exceptions import LLMError
from src.services.question_generator import QuestionGenerator


VALID_Q = {
    "text": "TCP 建立连接需要几次握手？",
    "option_a": "2 次", "option_b": "3 次", "option_c": "4 次", "option_d": "5 次",
    "correct_answer": "B",
    "explanation": "TCP 通过三次握手建立连接：SYN、SYN+ACK、ACK。",
    "difficulty": "basic",
    "layer": "传输层",
    "chapter": "5.1",
}

INVALID_ANSWER_Q = {**VALID_Q, "text": "另一道题", "correct_answer": "E"}
INVALID_LAYER_Q = {**VALID_Q, "text": "第三道题", "layer": "会话层"}


class FakeLLM:
    """invoke 返回固定 content 的假 ChatOpenAI"""

    def __init__(self, content=None, exc=None):
        self.content = content
        self.exc = exc

    def invoke(self, messages):
        if self.exc:
            raise self.exc
        msg = MagicMock()
        msg.content = self.content
        return msg


def make_generator(fake_llm, seed_rows=None):
    driver = MagicMock()
    session_mock = MagicMock()
    session_mock.run.return_value.data.return_value = seed_rows or []
    session_mock.__enter__ = MagicMock(return_value=session_mock)
    session_mock.__exit__ = MagicMock(return_value=False)
    driver.session.return_value = session_mock

    gen = QuestionGenerator(driver, llm=fake_llm)
    return gen, driver, session_mock


@pytest.fixture
def patch_entity_pool(monkeypatch):
    """把实体池替换为固定列表，避免真实 Neo4j 查询"""
    pool = {
        "entities": {
            "传输层": [
                {"name": "TCP", "name_en": "TCP", "entity_type": "Protocol",
                 "description": "传输控制协议，面向连接"},
                {"name": "UDP", "name_en": "UDP", "entity_type": "Protocol",
                 "description": "用户数据报协议"},
                {"name": "三次握手", "name_en": "", "entity_type": "Concept",
                 "description": "TCP 连接建立过程"},
            ],
            "网络层": [
                {"name": "IP", "name_en": "IP", "entity_type": "Protocol",
                 "description": "网际协议"},
            ],
        },
        "total": 4,
    }
    monkeypatch.setattr(
        "src.services.question_generator.EntityService.get_entities_by_layer",
        lambda self: pool,
    )
    monkeypatch.setattr(
        "src.services.question_generator.EntityService.get_all_entity_names",
        lambda self: ["TCP", "UDP", "三次握手"],
    )


class TestGenerate:
    def test_happy_path_with_references(self, patch_entity_pool):
        gen, _, _ = make_generator(FakeLLM("[" + str(VALID_Q).replace("'", '"') + "]"))
        result = gen.generate(layer="传输层", count=1)

        assert result["valid"] == 1
        q = result["questions"][0]
        assert q["correct_answer"] == "B"
        assert q["layer"] == "传输层"
        assert "TCP" in q["references"] or "三次握手" in q["references"]
        assert result["seed_entities"]

    def test_strips_code_fence(self, patch_entity_pool):
        payload = "```json\n[" + str(VALID_Q).replace("'", '"') + "]\n```"
        gen, _, _ = make_generator(FakeLLM(payload))
        result = gen.generate(layer="传输层", count=1)
        assert result["valid"] == 1

    def test_invalid_questions_filtered(self, patch_entity_pool):
        items = [VALID_Q, INVALID_ANSWER_Q]
        payload = "[" + ",".join(str(i).replace("'", '"') for i in items) + "]"
        gen, _, _ = make_generator(FakeLLM(payload))
        result = gen.generate(layer="传输层", count=3)
        assert result["generated"] == 2
        assert result["valid"] == 1

    def test_layer_override_when_specified(self, patch_entity_pool):
        items = [INVALID_LAYER_Q]
        payload = "[" + ",".join(str(i).replace("'", '"') for i in items) + "]"
        gen, _, _ = make_generator(FakeLLM(payload))
        result = gen.generate(layer="传输层", count=1)
        # 指定 layer 时非法 layer 被强制覆盖为指定层
        assert result["questions"][0]["layer"] == "传输层"

    def test_invalid_layer_without_spec_rejected(self, patch_entity_pool):
        items = [INVALID_LAYER_Q]
        payload = "[" + ",".join(str(i).replace("'", '"') for i in items) + "]"
        gen, _, _ = make_generator(FakeLLM(payload))
        with pytest.raises(LLMError):
            gen.generate(count=1)

    def test_duplicate_text_deduped(self, patch_entity_pool):
        items = [VALID_Q, VALID_Q]
        payload = "[" + ",".join(str(i).replace("'", '"') for i in items) + "]"
        gen, _, _ = make_generator(FakeLLM(payload))
        result = gen.generate(layer="传输层", count=2)
        assert result["valid"] == 1

    def test_llm_failure_raises_llm_error(self, patch_entity_pool):
        gen, _, _ = make_generator(FakeLLM(exc=RuntimeError("api down")))
        with pytest.raises(LLMError):
            gen.generate(count=1)

    def test_unparseable_output_raises(self, patch_entity_pool):
        gen, _, _ = make_generator(FakeLLM("这不是 JSON"))
        with pytest.raises(LLMError):
            gen.generate(count=1)


class TestPickSeeds:
    def test_user_seeds_queried_and_missing_ignored(self, patch_entity_pool):
        rows = [{"name": "TCP", "description": "传输控制协议", "layer": "传输层"}]
        gen, _, session_mock = make_generator(FakeLLM("[]"), seed_rows=rows)
        seeds = gen._pick_seeds(None, ["TCP", "不存在的实体"], 5)
        assert [s["name"] for s in seeds] == ["TCP"]

    def test_no_seeds_no_pool_raises(self, monkeypatch):
        monkeypatch.setattr(
            "src.services.question_generator.EntityService.get_entities_by_layer",
            lambda self: {"entities": {}, "total": 0},
        )
        gen, _, _ = make_generator(FakeLLM("[]"))
        with pytest.raises(LLMError):
            gen.generate(count=1)
