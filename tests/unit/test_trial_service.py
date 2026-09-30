"""TrialService 单元测试 — 平行卷生成、作答批改、效果分析、LLM 报告（mock）"""

import pytest
from unittest.mock import MagicMock, patch

from src.mastery_tracker import MasteryTracker
from src.services.trial_service import MCQ_LAYERS, TrialService


LAYERS = MCQ_LAYERS


def build_bank(per_layer_pool: int = 12) -> dict:
    """构造伪题库：每层 per_layer_pool 道题，correct_answer 统一为 'A'"""
    bank = {}
    for li, layer in enumerate(LAYERS):
        for qi in range(per_layer_pool):
            qid = f"q_{li}_{qi}"
            bank[qid] = {
                "id": qid,
                "text": f"{layer} 第 {qi} 题",
                "option_a": "选项A", "option_b": "选项B",
                "option_c": "选项C", "option_d": "选项D",
                "layer": layer,
                "difficulty": "basic",
                "chapter": f"{li + 1}.{qi}",
                "correct_answer": "A",
                "explanation": "解析",
            }
    return bank


class FakeSession:
    def __init__(self, bank):
        self._bank = bank

    def run(self, query, **params):
        if "q.layer IN $layers" in query:
            grouped = {}
            for qid, q in self._bank.items():
                grouped.setdefault(q["layer"], []).append(qid)
            return [
                {"layer": layer, "ids": ids}
                for layer, ids in grouped.items()
                if layer in params["layers"]
            ]
        if "q.layer = $layer" in query:
            layer = params["layer"]
            return [{"id": qid} for qid, q in self._bank.items() if q["layer"] == layer]
        if "q.id IN $ids" in query:
            records = [dict(self._bank[qid]) for qid in params["ids"] if qid in self._bank]
            if "correct_answer" not in query:
                for r in records:
                    r.pop("correct_answer", None)
                    r.pop("explanation", None)
            return records
        return []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeDriver:
    def __init__(self, bank):
        self._bank = bank

    def session(self):
        return FakeSession(self._bank)


@pytest.fixture
def tracker(tmp_path):
    t = MasteryTracker(db_path=str(tmp_path / "mastery.db"))
    yield t
    t.close()


@pytest.fixture
def service(tracker):
    return TrialService(FakeDriver(build_bank()), tracker)


def complete_trial(service, tracker, name="第一批", per_layer=2, seed=42):
    """建批次 + 登记被试，返回 (trial_id, codes)"""
    created = service.create_trial(name, per_layer=per_layer, seed=seed)
    codes = [service.add_participant(created["trial_id"], f"P{i:04d}")["code"] for i in range(2)]
    return created["trial_id"], codes


def take_test(service, code, phase, wrong_positions=()):
    """作答一个阶段：全选 A，wrong_positions 指定第几题（0 基）故意答错"""
    started = service.start_test(code, phase)
    answers = {}
    for idx, q in enumerate(started["questions"]):
        answers[q["id"]] = "B" if idx in wrong_positions else "A"
    return service.submit_test(started["test_id"], answers)


class TestCreateTrial:
    def test_reproducible_with_same_seed(self, service):
        a = service.create_trial("A", per_layer=2, seed=42)
        b = service.create_trial("B", per_layer=2, seed=42)
        assert a["pre_ids"] == b["pre_ids"]
        assert a["post_ids"] == b["post_ids"]

    def test_pre_post_disjoint_and_stratified(self, service):
        created = service.create_trial("T", per_layer=3, seed=7)
        assert set(created["pre_ids"]).isdisjoint(created["post_ids"])

        trial = service._tracker.get_trial(created["trial_id"])
        pools = build_bank()
        for phase in ("pre_ids", "post_ids"):
            per_layer_count = {}
            for qid in trial["config"][phase]:
                layer = pools[qid]["layer"]
                per_layer_count[layer] = per_layer_count.get(layer, 0) + 1
            assert per_layer_count == {layer: 3 for layer in LAYERS}

    def test_insufficient_pool_raises(self, service):
        with pytest.raises(ValueError, match="题库不足"):
            service.create_trial("T", per_layer=10, seed=1)  # 需 20/层，池仅 12

    def test_unknown_trial_errors(self, service):
        with pytest.raises(ValueError, match="不存在"):
            service.add_participant("trial_nothing")
        with pytest.raises(ValueError, match="无效"):
            service.start_test("PXXXX", "pre")


class TestTakingTests:
    def test_start_test_omits_answers(self, service):
        trial_id, codes = complete_trial(service, service._tracker)
        started = service.start_test(codes[0], "pre")
        assert started["question_count"] == 10  # 2 × 5 层
        for q in started["questions"]:
            assert "correct_answer" not in q
            assert "explanation" not in q

    def test_submit_grades_correctly(self, service):
        trial_id, codes = complete_trial(service, service._tracker)
        result = take_test(service, codes[0], "pre", wrong_positions=(0, 3))
        assert result["correct_count"] == 8
        assert result["total_count"] == 10
        assert result["accuracy"] == pytest.approx(0.8)
        wrong = [d for d in result["details"] if not d["correct"]]
        assert len(wrong) == 2
        assert wrong[0]["selected_answer"] == "B"
        assert wrong[0]["correct_answer"] == "A"

    def test_post_blocked_until_pre_submitted(self, service):
        trial_id, codes = complete_trial(service, service._tracker)
        with pytest.raises(ValueError, match="前测"):
            service.start_test(codes[0], "post")
        take_test(service, codes[0], "pre")
        assert service.start_test(codes[0], "post")["phase"] == "post"

    def test_double_submit_rejected(self, service):
        trial_id, codes = complete_trial(service, service._tracker)
        started = service.start_test(codes[0], "pre")
        service.submit_test(started["test_id"], {})
        with pytest.raises(ValueError, match="已提交"):
            service.submit_test(started["test_id"], {})

    def test_auto_code_generation(self, service):
        created = service.create_trial("T", per_layer=2, seed=1)
        p = service.add_participant(created["trial_id"])
        assert p["code"].startswith("P") and len(p["code"]) == 5


class TestJoin:
    def test_join_binds_session_and_reports_status(self, service):
        trial_id, codes = complete_trial(service, service._tracker)
        joined = service.join(codes[0], "sess-1")
        assert joined["participant"]["session_id"] == "sess-1"
        assert joined["pre_done"] is False and joined["post_done"] is False

        take_test(service, codes[0], "pre")
        joined = service.join(codes[0], "sess-1")  # 幂等重入
        assert joined["pre_done"] is True

    def test_join_unknown_code(self, service):
        with pytest.raises(ValueError, match="无效"):
            service.join("PXXXX", "sess")


class TestAnalysis:
    def test_aggregate_stats(self, service):
        trial_id, codes = complete_trial(service, service._tracker)
        # P0000：前测 60% → 后测 100%；P0001：前测 60% → 后测 60%
        take_test(service, codes[0], "pre", wrong_positions=(0, 1, 3, 4))
        take_test(service, codes[1], "pre", wrong_positions=(0, 1, 3, 4))
        take_test(service, codes[0], "post")
        take_test(service, codes[1], "post", wrong_positions=(0, 1, 3, 4))

        service.join(codes[0], "sess-A")
        service._tracker.record_review("sess-A", "TCP", quality=4, event_type="quiz_correct")
        service._tracker.record_feedback("sess-A", rating=1, question="?")

        analysis = service.get_analysis(trial_id)
        agg = analysis["aggregate"]
        assert agg["n"] == 2
        assert agg["mean_pre"] == pytest.approx(0.6)
        assert agg["mean_post"] == pytest.approx(0.8)
        assert agg["mean_gain"] == pytest.approx(0.2)
        assert agg["improvement_rate"] == pytest.approx(0.5)
        assert agg["small_sample_caveat"] is True

        completed = [p for p in analysis["participants"] if p["completed"]]
        by_code = {p["code"]: p for p in completed}
        assert by_code[codes[0]]["gain"] == pytest.approx(0.4)
        assert by_code[codes[0]]["behavior"]["total_events"] == 1
        assert by_code[codes[0]]["satisfaction"]["total"] == 1
        assert by_code[codes[1]]["behavior"] is None  # 未绑定会话

    def test_incomplete_participants_pending(self, service):
        trial_id, codes = complete_trial(service, service._tracker)
        take_test(service, codes[0], "pre", wrong_positions=(0,))
        analysis = service.get_analysis(trial_id)
        assert analysis["aggregate"]["n"] == 0
        assert all(not p["completed"] for p in analysis["participants"])
        assert analysis["aggregate"]["mean_gain"] is None

    def test_layer_gain_reported(self, service):
        trial_id, codes = complete_trial(service, service._tracker)
        take_test(service, codes[0], "pre", wrong_positions=(0, 1))  # 第 0 层全错
        take_test(service, codes[0], "post")
        analysis = service.get_analysis(trial_id)
        entry = next(p for p in analysis["participants"] if p["code"] == codes[0])
        first_layer = LAYERS[0]
        assert entry["layer_gain"][first_layer]["gain"] == pytest.approx(1.0)
        assert analysis["aggregate"]["layer_gain"][first_layer]["mean_gain"] == pytest.approx(1.0)


class TestReport:
    def test_generate_report_via_llm(self, service):
        trial_id, codes = complete_trial(service, service._tracker)
        take_test(service, codes[0], "pre", wrong_positions=(0, 1))
        take_test(service, codes[0], "post")

        fake_response = MagicMock()
        fake_response.choices = [MagicMock()]
        fake_response.choices[0].message.content = "```markdown\n# 用户试用评估报告\n正文\n```"
        with patch("src.services.trial_service.OpenAI") as mock_openai:
            mock_openai.return_value.chat.completions.create.return_value = fake_response
            report = service.generate_report(trial_id)

        assert report == "# 用户试用评估报告\n正文"
        prompt = mock_openai.return_value.chat.completions.create.call_args.kwargs[
            "messages"
        ][0]["content"]
        assert "用户试用评估报告" in prompt
        assert "n=" in prompt

    def test_generate_report_returns_none_on_error(self, service):
        trial_id, _ = complete_trial(service, service._tracker)
        with patch("src.services.trial_service.OpenAI", side_effect=Exception("boom")):
            assert service.generate_report(trial_id) is None
