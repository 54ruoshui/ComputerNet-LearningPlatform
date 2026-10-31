"""API Key 认证中间件单元测试"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.testclient import TestClient

from web.middleware import APIKeyMiddleware, PUBLIC_PATHS, VisitorMiddleware


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

    @app.get("/api/auth/me")
    async def auth_me():
        return {"user": None}

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
    def test_login_page_no_auth(self, mock_get_settings, client):
        mock_get_settings.return_value = MagicMock(api_key="secret123")
        resp = client.get("/login")
        # 最小 app 未注册 /login 路由，但中间件应放行（404 而非 401 证明未拦截）
        assert resp.status_code == 404

    @patch("web.middleware.get_settings")
    def test_auth_prefix_no_auth(self, mock_get_settings, client):
        mock_get_settings.return_value = MagicMock(api_key="secret123")
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200

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


# ---------- 访客 cookie（无账号体系下的浏览器身份） ----------

@pytest.fixture
def visitor_app():
    """挂 VisitorMiddleware 的最小应用：回显 scope 中的访客 ID"""
    from fastapi import FastAPI, Request

    app = FastAPI()
    app.add_middleware(VisitorMiddleware)

    @app.get("/whoami")
    async def whoami(request: Request):
        return {"visitor": request.scope.get("aigc_visitor")}

    return app


class TestVisitorMiddleware:

    def test_first_visit_sets_cookie(self, visitor_app):
        client = TestClient(visitor_app)
        resp = client.get("/whoami")
        assert resp.status_code == 200
        cookie = resp.headers.get("set-cookie", "")
        assert "aigc_visitor=" in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=Lax" in cookie
        assert "Path=/" in cookie
        assert "Max-Age=2592000" in cookie

    def test_cookie_stable_across_requests(self, visitor_app):
        client = TestClient(visitor_app)
        first = client.get("/whoami")
        visitor = first.json()["visitor"]
        assert len(visitor) == 32  # uuid4().hex

        second = client.get("/whoami")  # TestClient 持久化 cookie，自动回带
        assert second.json()["visitor"] == visitor
        assert "set-cookie" not in second.headers

    def test_distinct_clients_get_distinct_visitors(self, visitor_app):
        a = TestClient(visitor_app).get("/whoami").json()["visitor"]
        b = TestClient(visitor_app).get("/whoami").json()["visitor"]
        assert a != b

    def test_existing_cookie_respected(self, visitor_app):
        client = TestClient(visitor_app)
        client.cookies.set("aigc_visitor", "my-id")
        resp = client.get("/whoami")
        assert resp.json()["visitor"] == "my-id"
        assert "set-cookie" not in resp.headers
