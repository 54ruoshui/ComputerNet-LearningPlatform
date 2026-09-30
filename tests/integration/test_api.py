"""FastAPI 端到端集成测试 — httpx + TestClient"""

import json
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient


# ---------- Fixtures ----------

@pytest.fixture
def mock_engine():
    """Mock GraphRAGAgent"""
    engine = MagicMock()
    engine.query.return_value = {
        "question": "什么是TCP?",
        "answer": "TCP是传输控制协议",
        "processing_time": 0.5,
        "session_id": "test-session-id",
        "graph_data": {"nodes": [], "relationships": []},
    }
    engine.list_sessions.return_value = ["test-session-id"]
    engine.get_session_history.return_value = [
        {"role": "user", "content": "什么是TCP?"},
        {"role": "assistant", "content": "TCP是传输控制协议"},
    ]
    engine.new_session.return_value = "new-session-id"
    engine.delete_session.return_value = True
    engine.get_stats.return_value = {
        "entity_count": 100,
        "relationship_count": 200,
    }

    # Mock 服务属性
    engine.entity_retriever = MagicMock()
    engine.entity_retriever.get_neighbors.return_value = [{"name": "IP"}]
    engine.entity_retriever.keyword_search.return_value = [{"name": "TCP"}]
    engine.entity_retriever.get_subgraph_by_query.return_value = {
        "nodes": [], "relationships": []
    }
    engine.stats_service = MagicMock()
    engine.stats_service.get_stats.return_value = {
        "entity_count": 100,
        "relationship_count": 200,
    }

    engine.driver = MagicMock()

    session_mock = MagicMock()
    session_mock.run.return_value = [
        {"name": "TCP", "entity_type": "Protocol", "description": "传输控制协议"}
    ]
    session_mock.__enter__ = MagicMock(return_value=session_mock)
    session_mock.__exit__ = MagicMock(return_value=False)
    engine.driver.session.return_value = session_mock

    return engine


@pytest.fixture
def client(mock_engine):
    """FastAPI TestClient — 直接注入 mock engine 和 service，跳过真实初始化"""
    import web.graph_rag_web as web_mod
    from src.services.entity_service import EntityService
    from src.services.mastery_service import MasteryService

    # 保存原始值
    orig = {
        "engine": web_mod.langgraph_engine,
        "image": web_mod.image_generator,
        "entity_service": web_mod.entity_service,
        "mastery_service": web_mod.mastery_service,
    }

    # 注入 mock engine 和 service
    web_mod.langgraph_engine = mock_engine
    web_mod.image_generator = None
    web_mod.entity_service = EntityService(mock_engine.driver)
    web_mod.mastery_service = MasteryService(None)

    with patch("web.middleware.get_settings") as mock_mw:
        mock_mw.return_value = MagicMock(api_key="")

        c = TestClient(web_mod.app)
        yield c

    # 恢复
    web_mod.langgraph_engine = orig["engine"]
    web_mod.image_generator = orig["image"]
    web_mod.entity_service = orig["entity_service"]
    web_mod.mastery_service = orig["mastery_service"]


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """每个测试前清空限流桶，避免限流状态跨测试泄漏（如 /api/quiz/generate 5次/60s）"""
    from web.rate_limiter import _rate_limiter

    _rate_limiter._buckets.clear()
    yield
    _rate_limiter._buckets.clear()


@pytest.fixture
def client_with_tracker(mock_engine, tmp_path):
    """注入带真实 MasteryTracker 的 MasteryService，测反馈端点正向路径"""
    import web.graph_rag_web as web_mod
    from src.mastery_tracker import MasteryTracker
    from src.services.mastery_service import MasteryService

    tracker = MasteryTracker(db_path=str(tmp_path / "mastery.db"))
    orig = {
        "engine": web_mod.langgraph_engine,
        "mastery_service": web_mod.mastery_service,
    }
    web_mod.langgraph_engine = mock_engine
    web_mod.mastery_service = MasteryService(tracker)

    with patch("web.middleware.get_settings") as mock_mw:
        mock_mw.return_value = MagicMock(api_key="")
        c = TestClient(web_mod.app)
        yield c

    web_mod.langgraph_engine = orig["engine"]
    web_mod.mastery_service = orig["mastery_service"]
    tracker.close()


@pytest.fixture
def client_with_quiz(mock_engine):
    """注入 mock quiz_service 与 question_generator，测习题生成/入库端点"""
    import web.graph_rag_web as web_mod

    mock_quiz = MagicMock()
    mock_quiz.add_question.return_value = "mcq_abc12345"
    mock_gen = MagicMock()
    mock_gen.generate.return_value = {
        "questions": [{
            "text": "TCP 建立连接需要几次握手？",
            "option_a": "2 次", "option_b": "3 次", "option_c": "4 次", "option_d": "5 次",
            "correct_answer": "B", "explanation": "SYN、SYN+ACK、ACK",
            "difficulty": "basic", "layer": "传输层", "chapter": "5.1",
            "references": ["TCP"],
        }],
        "seed_entities": ["TCP"],
        "generated": 1,
        "valid": 1,
    }

    orig = {
        "engine": web_mod.langgraph_engine,
        "quiz_service": web_mod.quiz_service,
        "question_generator": web_mod.question_generator,
    }
    web_mod.langgraph_engine = mock_engine
    web_mod.quiz_service = mock_quiz
    web_mod.question_generator = mock_gen

    with patch("web.middleware.get_settings") as mock_mw:
        mock_mw.return_value = MagicMock(api_key="")
        c = TestClient(web_mod.app)
        yield c, mock_quiz, mock_gen

    web_mod.langgraph_engine = orig["engine"]
    web_mod.quiz_service = orig["quiz_service"]
    web_mod.question_generator = orig["question_generator"]


# ---------- Health ----------

class TestHealthEndpoint:

    def test_health_returns_200(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"
        assert "timestamp" in body


# ---------- Query ----------

class TestQueryEndpoint:

    def test_query_success(self, client, mock_engine):
        resp = client.post("/api/query", json={
            "question": "什么是TCP?",
            "session_id": "test-session",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["answer"] == "TCP是传输控制协议"
        assert body["session_id"] == "test-session-id"

    def test_query_missing_question(self, client):
        resp = client.post("/api/query", json={})
        assert resp.status_code == 422

    def test_query_empty_question(self, client):
        resp = client.post("/api/query", json={"question": ""})
        assert resp.status_code == 422

    def test_query_too_long(self, client):
        resp = client.post("/api/query", json={"question": "x" * 2001})
        assert resp.status_code == 422

    def test_query_without_session_id(self, client, mock_engine):
        resp = client.post("/api/query", json={"question": "test"})
        assert resp.status_code == 200
        mock_engine.query.assert_called_once()


# ---------- Sessions ----------

class TestSessionEndpoints:

    def test_list_sessions(self, client, mock_engine):
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        body = resp.json()
        assert "sessions" in body

    def test_new_session(self, client, mock_engine):
        resp = client.post("/api/sessions/new")
        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"] == "new-session-id"

    def test_session_history(self, client, mock_engine):
        resp = client.get("/api/sessions/test-session-id/history")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["history"]) == 2

    def test_delete_session(self, client, mock_engine):
        mock_engine.delete_session.return_value = True
        resp = client.delete("/api/sessions/test-session-id")
        assert resp.status_code == 200

    def test_delete_nonexistent_session(self, client, mock_engine):
        mock_engine.delete_session.return_value = False
        resp = client.delete("/api/sessions/no-such-session")
        assert resp.status_code == 404


# ---------- Graph Stats ----------

class TestGraphStatsEndpoint:

    def test_graph_stats(self, client, mock_engine):
        resp = client.get("/api/graph_stats")
        assert resp.status_code == 200
        body = resp.json()
        assert body["entity_count"] == 100


# ---------- Search Nodes ----------

class TestSearchNodesEndpoint:

    def test_search_with_query(self, client, mock_engine):
        resp = client.get("/api/search_nodes", params={"q": "TCP"})
        assert resp.status_code == 200
        body = resp.json()
        assert "nodes" in body

    def test_search_empty_query(self, client, mock_engine):
        resp = client.get("/api/search_nodes", params={"q": ""})
        assert resp.status_code == 200
        assert resp.json()["nodes"] == []


# ---------- Node Neighbors ----------

class TestNodeNeighborsEndpoint:

    def test_get_neighbors(self, client, mock_engine):
        resp = client.get("/api/node_neighbors/TCP")
        assert resp.status_code == 200
        body = resp.json()
        assert "neighbors" in body


# ---------- Config ----------

class TestConfigEndpoint:

    def test_config(self, client):
        resp = client.get("/api/config")
        assert resp.status_code == 200
        body = resp.json()
        assert "features" in body
        assert "limits" in body


# ---------- Mastery (503 when not initialized) ----------

class TestMasteryEndpoints:

    def test_mastery_returns_503_when_not_initialized(self, client):
        resp = client.get("/api/mastery/test-session")
        assert resp.status_code == 503

    def test_set_mastery_returns_503_when_not_initialized(self, client):
        resp = client.post("/api/mastery", json={
            "session_id": "s1",
            "entity_name": "TCP",
            "mastered": True,
        })
        assert resp.status_code == 503


# ---------- Answer Feedback ----------

class TestFeedbackEndpoints:

    def test_feedback_returns_503_when_not_initialized(self, client):
        resp = client.post("/api/feedback", json={"rating": 1})
        assert resp.status_code == 503

    def test_feedback_stats_returns_503_when_not_initialized(self, client):
        resp = client.get("/api/feedback/stats")
        assert resp.status_code == 503

    def test_invalid_rating_rejected(self, client_with_tracker):
        resp = client_with_tracker.post("/api/feedback", json={"rating": 2})
        assert resp.status_code == 422

    def test_submit_and_stats(self, client_with_tracker):
        resp = client_with_tracker.post("/api/feedback", json={
            "session_id": "s1", "question": "TCP 三次握手的过程是什么？", "rating": 1,
        })
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        resp = client_with_tracker.post("/api/feedback", json={"rating": 0})
        assert resp.status_code == 200

        stats = client_with_tracker.get("/api/feedback/stats").json()
        assert stats["total"] == 2
        assert stats["positive"] == 1
        assert stats["satisfaction_rate"] == 0.5

        stats = client_with_tracker.get(
            "/api/feedback/stats", params={"session_id": "s1"}
        ).json()
        assert stats["total"] == 1
        assert stats["satisfaction_rate"] == 1.0


# ---------- Quiz Generation ----------

class TestQuizGenerateEndpoint:

    def test_generate_success(self, client_with_quiz):
        client, _, mock_gen = client_with_quiz
        resp = client.post("/api/quiz/generate", json={"layer": "传输层", "count": 1})
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] == 1
        assert body["questions"][0]["correct_answer"] == "B"
        mock_gen.generate.assert_called_once_with(
            layer="传输层", chapter=None, difficulty=None, count=1, seed_entities=None)

    def test_generate_with_seeds_and_difficulty(self, client_with_quiz):
        client, _, _ = client_with_quiz
        resp = client.post("/api/quiz/generate", json={
            "seed_entities": ["TCP", "UDP"], "difficulty": "hard", "count": 3,
        })
        assert resp.status_code == 200

    def test_generate_count_too_small(self, client_with_quiz):
        client, _, _ = client_with_quiz
        resp = client.post("/api/quiz/generate", json={"count": 0})
        assert resp.status_code == 422

    def test_generate_count_too_large(self, client_with_quiz):
        client, _, _ = client_with_quiz
        resp = client.post("/api/quiz/generate", json={"count": 11})
        assert resp.status_code == 422

    def test_generate_invalid_layer(self, client_with_quiz):
        client, _, _ = client_with_quiz
        resp = client.post("/api/quiz/generate", json={"layer": "会话层"})
        assert resp.status_code == 422

    def test_generate_returns_503_when_not_initialized(self, client, mock_engine):
        # 默认 client 注入 engine 但 question_generator 为 None，且 lazy-init 构造失败会置 None
        import web.graph_rag_web as web_mod
        orig = web_mod.question_generator
        web_mod.question_generator = None
        try:
            resp = client.post("/api/quiz/generate", json={"count": 1})
            assert resp.status_code in (503, 500)
        finally:
            web_mod.question_generator = orig


class TestCreateQuestionSource:

    VALID = {
        "text": "UDP 是面向连接的协议吗？",
        "option_a": "是", "option_b": "不是", "option_c": "有时是", "option_d": "不确定",
        "correct_answer": "B",
        "explanation": "UDP 是无连接协议。",
        "difficulty": "basic",
        "layer": "传输层",
    }

    def test_source_ai_passthrough(self, client_with_quiz):
        client, mock_quiz, _ = client_with_quiz
        resp = client.post("/api/quiz/questions", json={**self.VALID, "source": "ai"})
        assert resp.status_code == 200
        assert resp.json()["question_id"] == "mcq_abc12345"
        _, kwargs = mock_quiz.add_question.call_args
        passed = mock_quiz.add_question.call_args[0][0]
        assert passed["source"] == "ai"

    def test_source_defaults_to_manual(self, client_with_quiz):
        client, mock_quiz, _ = client_with_quiz
        resp = client.post("/api/quiz/questions", json=self.VALID)
        assert resp.status_code == 200
        passed = mock_quiz.add_question.call_args[0][0]
        assert passed["source"] == "manual"

    def test_invalid_source_rejected(self, client_with_quiz):
        client, _, _ = client_with_quiz
        resp = client.post("/api/quiz/questions", json={**self.VALID, "source": "web"})
        assert resp.status_code == 422


# ---------- Error Handling ----------

class TestErrorHandling:

    def test_query_handles_engine_exception(self, client, mock_engine):
        mock_engine.query.side_effect = Exception("internal error")
        resp = client.post("/api/query", json={"question": "test"})
        assert resp.status_code == 500


# ---------- Trial (前测-后测试用评估) ----------

@pytest.fixture
def client_with_trial(mock_engine):
    """注入 mock trial_service，测 /api/trial/* 端点"""
    import web.graph_rag_web as web_mod

    mock_trial = MagicMock()
    mock_trial.create_trial.return_value = {
        "trial_id": "trial_abc12345", "name": "第一批试用",
        "per_layer": 4, "seed": 42, "question_count": 20,
        "pre_ids": ["q1"], "post_ids": ["q2"],
    }
    mock_trial.list_trials.return_value = [
        {"id": "trial_abc12345", "name": "第一批试用", "status": "active",
         "created_at": "2026-09-29", "participant_count": 1, "submitted_test_count": 1},
    ]
    mock_trial.add_participant.return_value = {
        "id": 1, "trial_id": "trial_abc12345", "code": "P0001",
    }
    mock_trial.list_participants.return_value = [
        {"id": 1, "code": "P0001", "session_id": "sess-1", "created_at": "2026-09-29",
         "pre_done": True, "post_done": False},
    ]
    mock_trial.join.return_value = {
        "participant": {"id": 1, "code": "P0001", "session_id": "sess-1"},
        "pre_done": True, "post_done": False,
    }
    mock_trial.start_test.return_value = {
        "test_id": 7, "trial_id": "trial_abc12345", "code": "P0001",
        "phase": "pre", "question_count": 1,
        "questions": [{"id": "q1", "text": "题干", "option_a": "a", "option_b": "b",
                       "option_c": "c", "option_d": "d", "layer": "物理层",
                       "difficulty": "basic", "chapter": "1.1"}],
    }
    mock_trial.submit_test.return_value = {
        "test_id": 7, "phase": "pre", "correct_count": 1, "total_count": 1,
        "accuracy": 1.0, "details": [],
    }
    mock_trial.get_analysis.return_value = {
        "trial_id": "trial_abc12345", "trial_name": "第一批试用",
        "participants": [], "aggregate": {"n": 0},
    }
    mock_trial.generate_report.return_value = "# 用户试用评估报告\n正文"

    orig = {
        "engine": web_mod.langgraph_engine,
        "trial_service": web_mod.trial_service,
    }
    web_mod.langgraph_engine = mock_engine
    web_mod.trial_service = mock_trial

    with patch("web.middleware.get_settings") as mock_mw:
        mock_mw.return_value = MagicMock(api_key="")
        c = TestClient(web_mod.app)
        yield c, mock_trial

    web_mod.langgraph_engine = orig["engine"]
    web_mod.trial_service = orig["trial_service"]


class TestTrialEndpoints:

    def test_returns_503_when_not_initialized(self, client):
        # client fixture: mastery_service 无 tracker → init 失败 → 503
        resp = client.get("/api/trial/list")
        assert resp.status_code == 503

    def test_create_trial(self, client_with_trial):
        client, mock_trial = client_with_trial
        resp = client.post("/api/trial/create", json={"name": "第一批试用", "per_layer": 4})
        assert resp.status_code == 200
        body = resp.json()
        assert body["trial_id"] == "trial_abc12345"
        assert body["question_count"] == 20
        mock_trial.create_trial.assert_called_once_with("第一批试用", per_layer=4, seed=None)

    def test_create_trial_invalid_per_layer_422(self, client_with_trial):
        client, _ = client_with_trial
        resp = client.post("/api/trial/create", json={"name": "x", "per_layer": 11})
        assert resp.status_code == 422

    def test_create_trial_insufficient_pool_400(self, client_with_trial):
        client, mock_trial = client_with_trial
        mock_trial.create_trial.side_effect = ValueError("题库不足")
        resp = client.post("/api/trial/create", json={"name": "x"})
        assert resp.status_code == 400
        assert "题库不足" in resp.json()["detail"]

    def test_list_trials(self, client_with_trial):
        client, _ = client_with_trial
        resp = client.get("/api/trial/list")
        assert resp.status_code == 200
        assert resp.json()["trials"][0]["id"] == "trial_abc12345"

    def test_add_and_list_participants(self, client_with_trial):
        client, mock_trial = client_with_trial
        resp = client.post("/api/trial/trial_abc12345/participants", json={})
        assert resp.status_code == 200
        assert resp.json()["code"] == "P0001"

        resp = client.get("/api/trial/trial_abc12345/participants")
        assert resp.status_code == 200
        assert resp.json()["participants"][0]["pre_done"] is True

    def test_join(self, client_with_trial):
        client, mock_trial = client_with_trial
        resp = client.post("/api/trial/join", json={"code": "P0001", "session_id": "sess-1"})
        assert resp.status_code == 200
        assert resp.json()["pre_done"] is True
        mock_trial.join.assert_called_once_with("P0001", "sess-1")

    def test_start_test_invalid_phase_422(self, client_with_trial):
        client, _ = client_with_trial
        resp = client.post("/api/trial/test/start", json={"code": "P0001", "phase": "mid"})
        assert resp.status_code == 422

    def test_start_and_submit_test(self, client_with_trial):
        client, mock_trial = client_with_trial
        resp = client.post("/api/trial/test/start", json={"code": "P0001", "phase": "pre"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["test_id"] == 7
        # 题目不含答案
        for q in body["questions"]:
            assert "correct_answer" not in q

        resp = client.post("/api/trial/test/submit", json={"test_id": 7, "answers": {"q1": "A"}})
        assert resp.status_code == 200
        assert resp.json()["accuracy"] == 1.0
        mock_trial.submit_test.assert_called_once_with(7, {"q1": "A"})

    def test_analysis_and_report(self, client_with_trial):
        client, _ = client_with_trial
        resp = client.get("/api/trial/trial_abc12345/analysis")
        assert resp.status_code == 200
        assert resp.json()["aggregate"]["n"] == 0

        resp = client.post("/api/trial/trial_abc12345/report")
        assert resp.status_code == 200
        assert "# 用户试用评估报告" in resp.json()["report"]

    def test_analysis_unknown_trial_404(self, client_with_trial):
        client, mock_trial = client_with_trial
        mock_trial.get_analysis.side_effect = ValueError("不存在")
        resp = client.get("/api/trial/trial_none/analysis")
        assert resp.status_code == 404

    def test_report_failure_502(self, client_with_trial):
        client, mock_trial = client_with_trial
        mock_trial.generate_report.return_value = None
        resp = client.post("/api/trial/trial_abc12345/report")
        assert resp.status_code == 502
