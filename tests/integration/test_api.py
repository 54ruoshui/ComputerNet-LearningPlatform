"""FastAPI 端到端集成测试 — httpx + TestClient"""

import json
import threading
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from web import concurrency as concurrency_mod
from web.concurrency import LLMConcurrencyGate


def install_gate(monkeypatch, max_concurrency: int, queue_timeout: float) -> LLMConcurrencyGate:
    """直接把小容量闸门装进单例位（不依赖环境变量与 Settings 缓存的时序）"""
    gate = LLMConcurrencyGate(max_concurrency, queue_timeout)
    monkeypatch.setattr(concurrency_mod, "_gate", gate)
    return gate


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
        "engine": web_mod.deps.langgraph_engine,
        "image": web_mod.deps.image_generator,
        "entity_service": web_mod.deps.entity_service,
        "mastery_service": web_mod.deps.mastery_service,
    }

    # 注入 mock engine 和 service
    web_mod.deps.langgraph_engine = mock_engine
    web_mod.deps.image_generator = None
    web_mod.deps.entity_service = EntityService(mock_engine.driver)
    web_mod.deps.mastery_service = MasteryService(None)

    with patch("web.middleware.get_settings") as mock_mw:
        mock_mw.return_value = MagicMock(api_key="")

        c = TestClient(web_mod.app)
        yield c

    # 恢复
    web_mod.deps.langgraph_engine = orig["engine"]
    web_mod.deps.image_generator = orig["image"]
    web_mod.deps.entity_service = orig["entity_service"]
    web_mod.deps.mastery_service = orig["mastery_service"]


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
        "engine": web_mod.deps.langgraph_engine,
        "mastery_service": web_mod.deps.mastery_service,
    }
    web_mod.deps.langgraph_engine = mock_engine
    web_mod.deps.mastery_service = MasteryService(tracker)

    with patch("web.middleware.get_settings") as mock_mw:
        mock_mw.return_value = MagicMock(api_key="")
        c = TestClient(web_mod.app)
        yield c

    web_mod.deps.langgraph_engine = orig["engine"]
    web_mod.deps.mastery_service = orig["mastery_service"]
    tracker.close()


@pytest.fixture
def client_with_sessions(mock_engine, tmp_path):
    """真实 MasteryTracker + mock engine，测会话归属按访客 cookie 隔离"""
    import web.graph_rag_web as web_mod
    from src.mastery_tracker import MasteryTracker
    from src.services.mastery_service import MasteryService

    tracker = MasteryTracker(db_path=str(tmp_path / "mastery.db"))
    orig = {
        "engine": web_mod.deps.langgraph_engine,
        "mastery_service": web_mod.deps.mastery_service,
    }
    web_mod.deps.langgraph_engine = mock_engine
    web_mod.deps.mastery_service = MasteryService(tracker)

    with patch("web.middleware.get_settings") as mock_mw:
        mock_mw.return_value = MagicMock(api_key="")
        c = TestClient(web_mod.app)
        yield c, tracker

    web_mod.deps.langgraph_engine = orig["engine"]
    web_mod.deps.mastery_service = orig["mastery_service"]
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
        "engine": web_mod.deps.langgraph_engine,
        "quiz_service": web_mod.deps.quiz_service,
        "question_generator": web_mod.deps.question_generator,
    }
    web_mod.deps.langgraph_engine = mock_engine
    web_mod.deps.quiz_service = mock_quiz
    web_mod.deps.question_generator = mock_gen

    with patch("web.middleware.get_settings") as mock_mw:
        mock_mw.return_value = MagicMock(api_key="")
        c = TestClient(web_mod.app)
        c.headers.update({"X-Admin-Token": "test-admin-token"})
        yield c, mock_quiz, mock_gen

    web_mod.deps.langgraph_engine = orig["engine"]
    web_mod.deps.quiz_service = orig["quiz_service"]
    web_mod.deps.question_generator = orig["question_generator"]


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


# ---------- 会话归属（访客 cookie 隔离） ----------

class TestSessionOwnership:

    def test_new_session_binds_to_visitor(self, client_with_sessions):
        client, tracker = client_with_sessions
        resp = client.post("/api/sessions/new")
        assert resp.status_code == 200
        sid = resp.json()["session_id"]
        assert tracker.get_session_owner(sid) == client.cookies.get("aigc_visitor")

    def test_list_sessions_scoped_to_owner(self, client_with_sessions, mock_engine):
        client, tracker = client_with_sessions
        client.post("/api/sessions/new")  # new-session-id 绑定到当前访客
        tracker.bind_session("stranger-session", "another-visitor")
        mock_engine.list_sessions.return_value = ["new-session-id", "stranger-session"]

        ids = [s["session_id"] for s in client.get("/api/sessions").json()["sessions"]]
        assert "new-session-id" in ids
        assert "stranger-session" not in ids

    def test_own_session_history_and_delete(self, client_with_sessions, mock_engine):
        client, tracker = client_with_sessions
        client.post("/api/sessions/new")  # 绑定 new-session-id

        assert client.get("/api/sessions/new-session-id/history").status_code == 200

        mock_engine.delete_session.return_value = True
        assert client.delete("/api/sessions/new-session-id").status_code == 200
        assert tracker.get_session_owner("new-session-id") is None  # 删除后解绑

    def test_foreign_session_history_delete_404(self, client_with_sessions):
        client, tracker = client_with_sessions
        tracker.bind_session("s-owned", "another-visitor")
        assert client.get("/api/sessions/s-owned/history").status_code == 404
        assert client.delete("/api/sessions/s-owned").status_code == 404

    def test_unbound_session_history_delete_404(self, client_with_sessions):
        # 未绑定会话（如他人私拼 ID）对当前访客不可见
        client, _ = client_with_sessions
        assert client.get("/api/sessions/never-bound/history").status_code == 404
        assert client.delete("/api/sessions/never-bound").status_code == 404

    def test_second_visitor_isolated(self, client_with_sessions, mock_engine):
        client, tracker = client_with_sessions
        client.post("/api/sessions/new")  # 访客 A 绑定 new-session-id

        import web.graph_rag_web as web_mod
        with patch("web.middleware.get_settings") as mock_mw:
            mock_mw.return_value = MagicMock(api_key="")
            other = TestClient(web_mod.app)  # 无 cookie 的全新 client = 访客 B

        mock_engine.list_sessions.return_value = ["new-session-id"]
        sessions = other.get("/api/sessions").json()["sessions"]
        assert [s["session_id"] for s in sessions] == []
        assert other.get("/api/sessions/new-session-id/history").status_code == 404

    def test_query_foreign_session_404(self, client_with_sessions):
        client, tracker = client_with_sessions
        tracker.bind_session("s-x", "another-visitor")
        resp = client.post("/api/query", json={"question": "q", "session_id": "s-x"})
        assert resp.status_code == 404

    def test_query_unbound_session_lazy_binds(self, client_with_sessions, mock_engine):
        """存量会话首次查询时懒绑定到当前访客（治愈升级前的旧会话）"""
        client, tracker = client_with_sessions
        resp = client.post("/api/query", json={"question": "q", "session_id": "legacy-1"})
        assert resp.status_code == 200
        assert tracker.get_session_owner("legacy-1") == client.cookies.get("aigc_visitor")

    def test_tracker_unavailable_fail_open(self, client, mock_engine):
        """client fixture 的 MasteryService 无 tracker → 归属校验放行（降级一致）"""
        assert client.get("/api/sessions/test-session-id/history").status_code == 200
        resp = client.post("/api/query", json={"question": "q", "session_id": "any-session"})
        assert resp.status_code == 200

    def test_user_data_routes_reject_foreign_session(self, client_with_sessions):
        """mastery/review/quiz 用户数据路由统一拒绝他人会话（404 不暴露存在性）"""
        client, tracker = client_with_sessions
        tracker.bind_session("s-victim", "another-visitor")
        assert client.get("/api/mastery/s-victim").status_code == 404
        assert client.post("/api/mastery", json={
            "session_id": "s-victim", "entity_name": "TCP", "mastered": True,
        }).status_code == 404
        assert client.get("/api/review/state/s-victim").status_code == 404
        assert client.get("/api/review/due/s-victim").status_code == 404
        assert client.get("/api/review/stats/s-victim").status_code == 404
        assert client.get("/api/quiz/done", params={"session_id": "s-victim"}).status_code == 404
        assert client.get("/api/quiz/wrong", params={"session_id": "s-victim"}).status_code == 404
        assert client.delete("/api/quiz/wrong", params={"session_id": "s-victim"}).status_code == 404

    def test_exam_and_dashboard_routes_reject_foreign_session(self, client_with_sessions):
        """exam/dashboard（含按所属会话校验的 result）同样拒绝他人会话"""
        import web.graph_rag_web as web_mod
        client, tracker = client_with_sessions
        tracker.bind_session("s-victim", "another-visitor")
        mock_exam = MagicMock()
        mock_exam.get_exam_history.return_value = {"history": []}
        mock_exam.get_exam_result.return_value = {"result_id": 1, "session_id": "s-victim"}
        mock_dash = MagicMock()
        mock_dash.get_overview.return_value = {}
        orig = {
            "exam": web_mod.deps.exam_service,
            "dash": web_mod.deps.dashboard_service,
        }
        web_mod.deps.exam_service = mock_exam
        web_mod.deps.dashboard_service = mock_dash
        try:
            assert client.get("/api/exam/history/s-victim").status_code == 404
            assert client.get("/api/exam/analysis/s-victim").status_code == 404
            assert client.get("/api/exam/recommendations/s-victim").status_code == 404
            assert client.get("/api/exam/result/1").status_code == 404
            assert client.get("/api/dashboard/overview/s-victim").status_code == 404
        finally:
            web_mod.deps.exam_service = orig["exam"]
            web_mod.deps.dashboard_service = orig["dash"]

    def test_mastery_route_lazy_binds_unbound_session(self, client_with_sessions):
        """存量未绑定会话在 mastery 路由首次访问时懒绑定到当前访客"""
        client, tracker = client_with_sessions
        assert client.get("/api/mastery/legacy-m").status_code == 200
        assert tracker.get_session_owner("legacy-m") == client.cookies.get("aigc_visitor")

    def test_shared_default_session_skips_ownership(self, client_with_sessions):
        """旧版共享 "default" 会话不参与归属校验（绑定会互相顶掉且无个人数据）"""
        client, tracker = client_with_sessions
        assert client.get("/api/quiz/done").status_code == 200
        assert client.get("/api/quiz/done", params={"session_id": "default"}).status_code == 200
        assert tracker.get_session_owner("default") is None


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
        orig = web_mod.deps.question_generator
        web_mod.deps.question_generator = None
        try:
            resp = client.post(
                "/api/quiz/generate", json={"count": 1},
                headers={"X-Admin-Token": "test-admin-token"},
            )
            assert resp.status_code in (503, 500)
        finally:
            web_mod.deps.question_generator = orig


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


# ---------- Admin Auth（X-Admin-Token） ----------

class TestAdminAuth:

    ADMIN = {"X-Admin-Token": "test-admin-token"}

    def test_admin_endpoints_reject_missing_token(self, client):
        assert client.get("/api/trial/list").status_code == 401
        assert client.post("/api/quiz/questions", json={}).status_code == 401
        assert client.post("/api/quiz/generate", json={"count": 1}).status_code == 401
        assert client.post("/api/exam/papers/import", json={}).status_code == 401

    def test_admin_endpoints_reject_wrong_token(self, client):
        resp = client.get("/api/trial/list", headers={"X-Admin-Token": "wrong"})
        assert resp.status_code == 401

    def test_correct_token_passes_auth(self, client):
        # 通过鉴权后落到服务未初始化（trial_service 为 None → 503），证明口令校验已放行
        resp = client.get("/api/trial/list", headers=self.ADMIN)
        assert resp.status_code == 503

    def test_missing_token_401_wins_over_body_validation(self, client):
        # 依赖先于 body 校验执行：无口令时即使 body 非法也返回 401 而非 422
        resp = client.post("/api/trial/create", json={"per_layer": "not-an-int"})
        assert resp.status_code == 401

    def test_student_endpoints_need_no_token(self, client):
        # join 是学生端点：无口令不应 401（此处服务未初始化为 503）
        resp = client.post("/api/trial/join", json={"code": "P0001", "session_id": "s"})
        assert resp.status_code != 401


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
        "session_id": "sess-1",
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
        "engine": web_mod.deps.langgraph_engine,
        "trial_service": web_mod.deps.trial_service,
    }
    web_mod.deps.langgraph_engine = mock_engine
    web_mod.deps.trial_service = mock_trial

    with patch("web.middleware.get_settings") as mock_mw:
        mock_mw.return_value = MagicMock(api_key="")
        c = TestClient(web_mod.app)
        c.headers.update({"X-Admin-Token": "test-admin-token"})
        yield c, mock_trial

    web_mod.deps.langgraph_engine = orig["engine"]
    web_mod.deps.trial_service = orig["trial_service"]


class TestQueryStreamEndpoint:

    def test_stream_yields_events_despite_body_sniff(self, client, mock_engine):
        """限流器 body 嗅探后重放 receive 不得伪造 http.disconnect，否则 StreamingResponse 立即被取消（SSE 空流）。

        TestClient 的 receive 不会主动派发 disconnect，复现不了；这里按 uvicorn 语义
        裸调 ASGI app：body 消息之后 receive 阻塞直到真实断连。
        """
        import asyncio
        import web.graph_rag_web as web_mod

        async def fake_stream(*args, **kwargs):
            await asyncio.sleep(0.1)  # 真实引擎首个事件前有检索等异步工作，确保竞态偏向伪 disconnect 一侧
            yield 'event: status\ndata: {"msg": "检索中"}\n\n'
            yield 'event: token\ndata: {"content": "TCP"}\n\n'
            yield 'event: done\ndata: {"session_id": "s1", "elapsed": 1.0}\n\n'
        mock_engine.query_stream = fake_stream

        body = json.dumps({"question": "什么是TCP？", "session_id": "s1"}).encode()
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "path": "/api/query/stream", "raw_path": b"/api/query/stream",
            "query_string": b"",
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
            "client": ("127.0.0.1", 12345), "server": ("test", 80), "scheme": "http",
        }
        pending_body = [{"type": "http.request", "body": body, "more_body": False}]

        async def receive():
            if pending_body:
                return pending_body.pop(0)
            await asyncio.sleep(3600)  # uvicorn 语义：body 后阻塞直到真实断连

        sent = []

        async def send(m):
            sent.append(m)

        async def run():
            await asyncio.wait_for(web_mod.app(scope, receive, send), timeout=30)

        asyncio.run(run())

        body_msgs = [m for m in sent if m["type"] == "http.response.body"]
        assert any(m.get("more_body") for m in body_msgs), \
            f"流式响应没有产出任何 body 消息（被伪造 disconnect 取消）: {[m['type'] for m in sent]}"
        payload = b"".join(m.get("body", b"") for m in body_msgs).decode()
        assert "event: status" in payload
        assert "event: token" in payload
        assert "event: done" in payload


class TestTrialEndpoints:

    def test_returns_503_when_not_initialized(self, client):
        # client fixture: mastery_service 无 tracker → init 失败 → 503
        resp = client.get(
            "/api/trial/list", headers={"X-Admin-Token": "test-admin-token"}
        )
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
        assert resp.json()["session_id"] == "sess-1"  # 服务端协商出的会话
        mock_trial.join.assert_called_once_with("P0001", "sess-1")

    def test_join_rate_limited(self, client_with_trial):
        """被试码端点 60/min 防枚举：第 61 次同 IP 请求 429"""
        client, _ = client_with_trial
        payload = {"code": "P0001", "session_id": "sess-1"}
        for _ in range(60):
            assert client.post("/api/trial/join", json=payload).status_code == 200
        resp = client.post("/api/trial/join", json=payload)
        assert resp.status_code == 429
        assert resp.headers["Retry-After"] == "60"

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

        resp = client.post("/api/trial/test/submit", json={
            "test_id": 7, "code": "P0001", "answers": {"q1": "A"}
        })
        assert resp.status_code == 200
        assert resp.json()["accuracy"] == 1.0
        mock_trial.submit_test.assert_called_once_with(7, {"q1": "A"}, "P0001")

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


# ---------- Quiz Done/Wrong（按 session 隔离的本地存储） ----------

@pytest.fixture
def client_with_quiz_storage(mock_engine, tmp_path):
    """注入带真实 MasteryTracker 的 MasteryService，测 /api/quiz/done|wrong 端点"""
    import web.graph_rag_web as web_mod
    from src.mastery_tracker import MasteryTracker
    from src.services.mastery_service import MasteryService

    tracker = MasteryTracker(db_path=str(tmp_path / "mastery.db"))
    orig = {
        "engine": web_mod.deps.langgraph_engine,
        "mastery_service": web_mod.deps.mastery_service,
    }
    web_mod.deps.langgraph_engine = mock_engine
    web_mod.deps.mastery_service = MasteryService(tracker)

    with patch("web.middleware.get_settings") as mock_mw:
        mock_mw.return_value = MagicMock(api_key="")
        c = TestClient(web_mod.app)
        yield c

    web_mod.deps.langgraph_engine = orig["engine"]
    web_mod.deps.mastery_service = orig["mastery_service"]
    tracker.close()


class TestQuizDoneWrongEndpoints:

    WRONG_BODY = {
        "id": "mcq_001",
        "text": "TCP 建立连接需要几次握手？",
        "option_a": "2 次", "option_b": "3 次", "option_c": "4 次", "option_d": "5 次",
        "correct_answer": "B", "explanation": "SYN / SYN+ACK / ACK",
        "layer": "传输层", "difficulty": "basic", "selected": "A",
        "time": "2026-09-30T10:00:00",
    }

    def test_done_mark_and_default_session(self, client_with_quiz_storage):
        client = client_with_quiz_storage
        assert client.get("/api/quiz/done").json()["ids"] == []
        resp = client.post("/api/quiz/done/mcq_001")
        assert resp.json()["ids"] == ["mcq_001"]
        # 不带 session_id 的请求落到 default 会话
        assert client.get("/api/quiz/done").json()["ids"] == ["mcq_001"]

    def test_done_isolated_between_sessions(self, client_with_quiz_storage):
        client = client_with_quiz_storage
        client.post("/api/quiz/done/mcq_001?session_id=alice")
        client.post("/api/quiz/done/mcq_002?session_id=bob")
        assert client.get("/api/quiz/done?session_id=alice").json()["ids"] == ["mcq_001"]
        assert client.get("/api/quiz/done?session_id=bob").json()["ids"] == ["mcq_002"]

    def test_wrong_add_overwrite_remove_clear(self, client_with_quiz_storage):
        client = client_with_quiz_storage
        body = {**self.WRONG_BODY, "session_id": "alice"}
        lst = client.post("/api/quiz/wrong", json=body).json()["list"]
        assert [w["id"] for w in lst] == ["mcq_001"]

        # 同 id 覆盖
        body["selected"] = "C"
        lst = client.post("/api/quiz/wrong", json=body).json()["list"]
        assert len(lst) == 1 and lst[0]["selected"] == "C"

        lst = client.delete("/api/quiz/wrong/mcq_001?session_id=alice").json()["list"]
        assert lst == []

        client.post("/api/quiz/wrong", json=body)
        assert client.delete("/api/quiz/wrong?session_id=alice").json()["success"] is True
        assert client.get("/api/quiz/wrong?session_id=alice").json()["list"] == []

    def test_wrong_isolated_between_sessions(self, client_with_quiz_storage):
        client = client_with_quiz_storage
        client.post("/api/quiz/wrong", json={**self.WRONG_BODY, "session_id": "alice"})
        assert client.get("/api/quiz/wrong?session_id=bob").json()["list"] == []

    def test_storage_unavailable_503(self, client, mock_engine):
        # client fixture 注入 MasteryService(None) → 不可用
        resp = client.get("/api/quiz/done")
        assert resp.status_code == 503
        resp = client.post("/api/quiz/wrong", json=self.WRONG_BODY)
        assert resp.status_code == 503


# ---------- LLM 并发闸门 ----------

@pytest.fixture
def client_with_exam(mock_engine):
    """注入 mock exam_service，测交卷 + 薄弱点分析闸门降级"""
    import web.graph_rag_web as web_mod

    mock_exam = MagicMock()
    mock_exam.submit_exam.return_value = {
        "paper_id": "paper-1", "score": 80,
        "details": [
            {"question_id": "q1", "correct": True},
            {"question_id": "q2", "correct": False},
        ],
    }
    mock_exam.analyze_weaknesses.return_value = {"weaknesses": ["网络层"]}

    orig = {
        "engine": web_mod.deps.langgraph_engine,
        "exam_service": web_mod.deps.exam_service,
    }
    web_mod.deps.langgraph_engine = mock_engine
    web_mod.deps.exam_service = mock_exam

    with patch("web.middleware.get_settings") as mock_mw:
        mock_mw.return_value = MagicMock(api_key="")
        c = TestClient(web_mod.app)
        yield c, mock_exam

    web_mod.deps.langgraph_engine = orig["engine"]
    web_mod.deps.exam_service = orig["exam_service"]


@pytest.fixture
def client_with_dashboard(mock_engine):
    """注入 mock dashboard_service，测学习报告端点"""
    import web.graph_rag_web as web_mod

    mock_dash = MagicMock()
    mock_dash.generate_report.return_value = "# 学习报告\n正文"

    orig = {
        "engine": web_mod.deps.langgraph_engine,
        "dashboard_service": web_mod.deps.dashboard_service,
    }
    web_mod.deps.langgraph_engine = mock_engine
    web_mod.deps.dashboard_service = mock_dash

    with patch("web.middleware.get_settings") as mock_mw:
        mock_mw.return_value = MagicMock(api_key="")
        c = TestClient(web_mod.app)
        yield c, mock_dash

    web_mod.deps.langgraph_engine = orig["engine"]
    web_mod.deps.dashboard_service = orig["dashboard_service"]


class TestQueryConcurrencyGate:

    def test_second_query_503_when_gate_full(self, client, mock_engine, monkeypatch):
        """max=1 且第一个查询阻塞时，第二个并发查询排队超时 → 503"""
        install_gate(monkeypatch, 1, 0.2)

        started = threading.Event()
        release = threading.Event()

        def slow_query(*args, **kwargs):
            started.set()
            release.wait(timeout=5)
            return {"question": "q", "answer": "a", "processing_time": 0.1, "session_id": "s"}

        mock_engine.query.side_effect = slow_query

        statuses = []

        def do_query():
            statuses.append(client.post("/api/query", json={"question": "q"}).status_code)

        first = threading.Thread(target=do_query)
        first.start()
        assert started.wait(timeout=2)  # 第一个请求已进入 LLM，闸门被占
        second = threading.Thread(target=do_query)
        second.start()
        second.join(timeout=5)
        release.set()
        first.join(timeout=5)

        assert sorted(statuses) == [200, 503]

    def test_stream_returns_sse_error_when_gate_full(self, client, mock_engine, monkeypatch):
        """闸满时流式端点返回单条 SSE error 事件（前端已有对应处理）"""
        gate = install_gate(monkeypatch, 1, 0.2)
        assert gate.acquire()  # 手动占满
        try:
            resp = client.post("/api/query/stream", json={"question": "q"})
            assert resp.status_code == 200
            assert "event: error" in resp.text
            assert "查询人数较多" in resp.text
        finally:
            gate.release()

    def test_dashboard_report_503_when_gate_full(self, client_with_dashboard, monkeypatch):
        client, _ = client_with_dashboard
        gate = install_gate(monkeypatch, 1, 0.2)
        assert gate.acquire()
        try:
            resp = client.post("/api/dashboard/report/some-session")
            assert resp.status_code == 503
        finally:
            gate.release()


class TestExamGateDegradation:

    def test_submit_skips_analysis_when_gate_full(self, client_with_exam, monkeypatch):
        """闸满时交卷照常判分，仅跳过 LLM 分析（analysis=None）"""
        client, mock_exam = client_with_exam
        gate = install_gate(monkeypatch, 1, 0.2)
        assert gate.acquire()  # 占满闸门模拟 LLM 高峰
        try:
            resp = client.post("/api/exam/submit", json={
                "session_id": "s1", "paper_id": "paper-1", "answers": {"q1": "A"},
            })
            assert resp.status_code == 200
            body = resp.json()
            assert body["score"] == 80  # 判分不受影响
            assert body["analysis"] is None  # 分析被优雅跳过
            mock_exam.analyze_weaknesses.assert_not_called()
        finally:
            gate.release()

    def test_submit_runs_analysis_when_gate_free(self, client_with_exam, monkeypatch):
        client, mock_exam = client_with_exam
        install_gate(monkeypatch, 1, 1.0)
        resp = client.post("/api/exam/submit", json={
            "session_id": "s1", "paper_id": "paper-1", "answers": {"q1": "A"},
        })
        assert resp.status_code == 200
        assert resp.json()["analysis"] == {"weaknesses": ["网络层"]}
        mock_exam.analyze_weaknesses.assert_called_once()
