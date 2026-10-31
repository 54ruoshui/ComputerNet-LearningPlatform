"""账号认证 API 集成测试 — 注册/登录/登出/me + cookie 属性 + 失败锁定（真实 AuthService + 真 tracker）"""

import pytest
from starlette.testclient import TestClient
from unittest.mock import MagicMock, patch

from src.mastery_tracker import MasteryTracker
from src.services.auth_service import AuthService
from src.services.mastery_service import MasteryService

# 复用 test_api 的 mock engine fixture（tests 是包，可直接导入）
from tests.integration.test_api import mock_engine  # noqa: F401


@pytest.fixture
def client_with_auth(mock_engine, tmp_path):
    """真实 MasteryTracker + 真实 AuthService（同一 tmp db）+ mock engine。

    yield (client, tracker, auth) — 测试可用 auth 直接预置账号/会话绑定。
    """
    import web.graph_rag_web as web_mod

    tracker = MasteryTracker(db_path=str(tmp_path / "mastery.db"))
    auth = AuthService(
        db_path=str(tmp_path / "mastery.db"), tracker=tracker,
        token_days=30, max_failures=3, lockout_minutes=10,
    )
    orig = {
        "engine": web_mod.deps.langgraph_engine,
        "mastery_service": web_mod.deps.mastery_service,
        "auth_service": web_mod.deps.auth_service,
    }
    web_mod.deps.langgraph_engine = mock_engine
    web_mod.deps.mastery_service = MasteryService(tracker)
    web_mod.deps.auth_service = auth

    with patch("web.middleware.get_settings") as mock_mw:
        mock_mw.return_value = MagicMock(api_key="")
        c = TestClient(web_mod.app)
        yield c, tracker, auth

    web_mod.deps.langgraph_engine = orig["engine"]
    web_mod.deps.mastery_service = orig["mastery_service"]
    web_mod.deps.auth_service = orig["auth_service"]
    auth.close()
    tracker.close()


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    from web.rate_limiter import _rate_limiter
    _rate_limiter._buckets.clear()
    yield
    _rate_limiter._buckets.clear()


class TestRegister:
    def test_register_returns_user_and_sets_cookie(self, client_with_auth):
        client, _, _ = client_with_auth
        resp = client.post("/api/auth/register", json={
            "email": "alice@qq.com", "password": "password123", "nickname": "小明",
        })
        assert resp.status_code == 201
        body = resp.json()
        assert body["user"]["email"] == "alice@qq.com"
        assert body["user"]["nickname"] == "小明"
        assert "password" not in str(body)
        cookie = resp.headers.get("set-cookie", "")
        assert "aigc_user=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie

    def test_register_duplicate_returns_409(self, client_with_auth):
        client, _, _ = client_with_auth
        payload = {"email": "bob@qq.com", "password": "password123", "nickname": "乙"}
        assert client.post("/api/auth/register", json=payload).status_code == 201
        resp = client.post("/api/auth/register", json=payload)
        assert resp.status_code == 409
        assert "已注册" in resp.json()["detail"]

    def test_register_invalid_email_422(self, client_with_auth):
        client, _, _ = client_with_auth
        resp = client.post("/api/auth/register", json={
            "email": "not-an-email", "password": "password123", "nickname": "丙",
        })
        assert resp.status_code == 422

    def test_register_short_password_422(self, client_with_auth):
        client, _, _ = client_with_auth
        resp = client.post("/api/auth/register", json={
            "email": "c@d.com", "password": "short", "nickname": "丁",
        })
        assert resp.status_code == 422


class TestLogin:
    def test_full_flow_register_me_logout(self, client_with_auth):
        client, _, _ = client_with_auth
        client.post("/api/auth/register", json={
            "email": "a@b.com", "password": "password123", "nickname": "小明",
        })
        me = client.get("/api/auth/me").json()
        assert me["user"]["nickname"] == "小明"

        logout = client.post("/api/auth/logout")
        assert logout.status_code == 200
        assert client.get("/api/auth/me").json()["user"] is None
        # 幂等：再次登出仍 200
        assert client.post("/api/auth/logout").status_code == 200

    def test_login_after_logout(self, client_with_auth):
        client, _, _ = client_with_auth
        client.post("/api/auth/register", json={
            "email": "a@b.com", "password": "password123", "nickname": "小明",
        })
        client.post("/api/auth/logout")
        resp = client.post("/api/auth/login", json={
            "email": "a@b.com", "password": "password123",
        })
        assert resp.status_code == 200
        assert client.get("/api/auth/me").json()["user"]["email"] == "a@b.com"

    def test_wrong_password_generic_401(self, client_with_auth):
        client, _, _ = client_with_auth
        client.post("/api/auth/register", json={
            "email": "a@b.com", "password": "password123", "nickname": "小明",
        })
        wrong = client.post("/api/auth/login", json={"email": "a@b.com", "password": "nope-nope"})
        unknown = client.post("/api/auth/login", json={"email": "ghost@x.com", "password": "whatever"})
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json()["detail"] == unknown.json()["detail"] == "邮箱或密码错误"

    def test_lockout_returns_429(self, client_with_auth):
        client, _, _ = client_with_auth
        client.post("/api/auth/register", json={
            "email": "a@b.com", "password": "password123", "nickname": "小明",
        })
        for _ in range(3):  # fixture 里 max_failures=3
            client.post("/api/auth/login", json={"email": "a@b.com", "password": "bad-bad"})
        resp = client.post("/api/auth/login", json={"email": "a@b.com", "password": "password123"})
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers


class TestMe:
    def test_guest_returns_null_user(self, client_with_auth):
        client, _, _ = client_with_auth
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        assert resp.json()["user"] is None

    def test_forged_token_returns_null_user(self, client_with_auth):
        client, _, _ = client_with_auth
        client.cookies.set("aigc_user", "forged-token-value")
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        assert resp.json()["user"] is None
