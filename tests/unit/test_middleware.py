"""API Key 认证中间件单元测试"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.testclient import TestClient

from web.middleware import APIKeyMiddleware, PUBLIC_PATHS


# ---------- 使用 FastAPI TestClient 测试中间件 ----------

@pytest.fixture
def test_app():
    """创建一个最小的 FastAPI 应用用于测试中间件"""
    from fastapi import FastAPI

    app = FastAPI()
    app.add_middleware(APIKeyMiddleware)

    @app.get("/")
    async def root():
        return {"msg": "ok"}

    @app.get("/api/health")
    async def health():
        return {"status": "healthy"}

    @app.get("/api/query")
    async def query():
        return {"result": "data"}

    @app.get("/api/sessions")
    async def sessions():
        return {"sessions": []}

    return app


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


# ---------- 开发模式（未配置 API_KEY） ----------

class TestDevMode:

    @patch("web.middleware.get_settings")
    def test_no_key_allows_all(self, mock_get_settings, client):
        mock_get_settings.return_value = MagicMock(api_key="")
        resp = client.get("/api/query")
        assert resp.status_code == 200

    @patch("web.middleware.get_settings")
    def test_no_key_health_ok(self, mock_get_settings, client):
        mock_get_settings.return_value = MagicMock(api_key="")
        resp = client.get("/api/health")
        assert resp.status_code == 200


# ---------- 生产模式（配置了 API_KEY） ----------

class TestAuthMode:

    @patch("web.middleware.get_settings")
    def test_valid_key_passes(self, mock_get_settings, client):
        mock_get_settings.return_value = MagicMock(api_key="secret123")
        resp = client.get("/api/query", headers={"X-API-Key": "secret123"})
        assert resp.status_code == 200

    @patch("web.middleware.get_settings")
    def test_invalid_key_blocked(self, mock_get_settings, client):
        mock_get_settings.return_value = MagicMock(api_key="secret123")
        resp = client.get("/api/query", headers={"X-API-Key": "wrong"})
        assert resp.status_code == 401

    @patch("web.middleware.get_settings")
    def test_missing_key_blocked(self, mock_get_settings, client):
        mock_get_settings.return_value = MagicMock(api_key="secret123")
        resp = client.get("/api/query")
        assert resp.status_code == 401

    @patch("web.middleware.get_settings")
    def test_empty_key_blocked(self, mock_get_settings, client):
        mock_get_settings.return_value = MagicMock(api_key="secret123")
        resp = client.get("/api/query", headers={"X-API-Key": ""})
        assert resp.status_code == 401


# ---------- 白名单路径 ----------

class TestPublicPaths:

    @patch("web.middleware.get_settings")
    def test_root_no_auth(self, mock_get_settings, client):
        mock_get_settings.return_value = MagicMock(api_key="secret123")
        resp = client.get("/")
        assert resp.status_code == 200

    @patch("web.middleware.get_settings")
    def test_health_no_auth(self, mock_get_settings, client):
        mock_get_settings.return_value = MagicMock(api_key="secret123")
        resp = client.get("/api/health")
        assert resp.status_code == 200

    @patch("web.middleware.get_settings")
    def test_docs_no_auth(self, mock_get_settings, client):
        mock_get_settings.return_value = MagicMock(api_key="secret123")
        resp = client.get("/docs")
        assert resp.status_code == 200

    @patch("web.middleware.get_settings")
    def test_openapi_no_auth(self, mock_get_settings, client):
        mock_get_settings.return_value = MagicMock(api_key="secret123")
        resp = client.get("/openapi.json")
        assert resp.status_code == 200

    @patch("web.middleware.get_settings")
    def test_api_query_requires_auth(self, mock_get_settings, client):
        mock_get_settings.return_value = MagicMock(api_key="secret123")
        resp = client.get("/api/query")
        assert resp.status_code == 401

    @patch("web.middleware.get_settings")
    def test_sessions_requires_auth(self, mock_get_settings, client):
        mock_get_settings.return_value = MagicMock(api_key="secret123")
        resp = client.get("/api/sessions")
        assert resp.status_code == 401


# ---------- 错误响应格式 ----------

class TestErrorResponse:

    @patch("web.middleware.get_settings")
    def test_error_response_format(self, mock_get_settings, client):
        mock_get_settings.return_value = MagicMock(api_key="secret123")
        resp = client.get("/api/query")
        assert resp.status_code == 401
        body = resp.json()
        assert "detail" in body
        assert "API Key" in body["detail"]
