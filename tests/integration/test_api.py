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


# ---------- Error Handling ----------

class TestErrorHandling:

    def test_query_handles_engine_exception(self, client, mock_engine):
        mock_engine.query.side_effect = Exception("internal error")
        resp = client.post("/api/query", json={"question": "test"})
        assert resp.status_code == 500
