"""QuizService 单元测试 — Cypher 批量化后的往返次数与行为回归"""

import pytest

from src.services.quiz_service import QuizService


class FakeResult:
    def __init__(self, records):
        self._records = list(records)

    def __iter__(self):
        return iter(self._records)

    def single(self):
        return self._records[0] if self._records else None


class FakeSession:
    """记录 run 调用；按查询子串返回预设行"""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def run(self, query, **params):
        self.calls.append((query, params))
        for marker, rows in self.responses.items():
            if marker in query:
                return FakeResult(rows)
        return FakeResult([])

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeDriver:
    def __init__(self, responses=None):
        self.session_obj = FakeSession(responses)

    def session(self):
        return self.session_obj


VALID = {
    "text": "TCP 建立连接需要几次握手？",
    "option_a": "2 次", "option_b": "3 次", "option_c": "4 次", "option_d": "5 次",
    "correct_answer": "B", "explanation": "SYN / SYN+ACK / ACK",
    "difficulty": "basic", "layer": "传输层",
}


class TestAddQuestion:

    def test_with_references_single_unwind(self):
        driver = FakeDriver()
        svc = QuizService(driver)

        q_id = svc.add_question({**VALID, "references": ["TCP", "IP"]})

        assert q_id.startswith("mcq_")
        calls = driver.session_obj.calls
        assert len(calls) == 3  # MERGE 题 / Layer 边 / UNWIND references 各一次
        refs_query, refs_params = calls[2]
        assert "UNWIND $refs" in refs_query
        assert refs_params["refs"] == ["TCP", "IP"]
        assert refs_params["id"] == q_id

    def test_without_references_skips_unwind(self):
        driver = FakeDriver()
        svc = QuizService(driver)

        svc.add_question(dict(VALID))

        assert len(driver.session_obj.calls) == 2  # 仅 MERGE 题 + Layer 边

    def test_blank_references_filtered(self):
        driver = FakeDriver()
        svc = QuizService(driver)

        svc.add_question({**VALID, "references": ["  ", "TCP", ""]})

        calls = driver.session_obj.calls
        assert len(calls) == 3
        assert calls[2][1]["refs"] == ["TCP"]


class TestGetStats:

    ROWS = [
        {"layer": "传输层", "difficulty": "basic", "chapter": "5.3"},
        {"layer": "传输层", "difficulty": "hard", "chapter": None},
        {"layer": "物理层", "difficulty": "basic", "chapter": "2.1"},
    ]

    def test_single_query_aggregation(self):
        driver = FakeDriver({"q.chapter AS chapter": self.ROWS})
        svc = QuizService(driver)

        stats = svc.get_stats()

        assert len(driver.session_obj.calls) == 1
        assert stats["total_questions"] == 3
        assert stats["by_layer"] == {"传输层": 2, "物理层": 1}
        assert stats["by_difficulty"] == {"basic": 2, "hard": 1}
        assert stats["by_chapter"] == {"5.3": 1, "2.1": 1}  # None 章节跳过

    def test_empty_bank(self):
        driver = FakeDriver({"q.chapter AS chapter": []})
        svc = QuizService(driver)

        stats = svc.get_stats()
        assert stats == {
            "total_questions": 0,
            "by_layer": {},
            "by_difficulty": {},
            "by_chapter": {},
        }


class TestCheckAnswer:

    def test_returns_fields_and_filters_empty_references(self):
        driver = FakeDriver({
            "correct_answer": [{
                "correct_answer": "B",
                "explanation": "三次握手",
                "layer": "传输层",
                "difficulty": "basic",
                "chapter": None,
                "references": ["TCP", "", None],
            }],
        })
        svc = QuizService(driver)

        result = svc.check_answer("mcq_1")

        assert result["correct_answer"] == "B"
        assert result["chapter"] == ""
        assert result["references"] == ["TCP"]

    def test_missing_question_returns_none(self):
        driver = FakeDriver({"correct_answer": []})
        svc = QuizService(driver)

        assert svc.check_answer("nope") is None
