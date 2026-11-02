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


class TestSessionRecovery:
    """跨设备找回：登录返回最新会话并 claim 到当前访客；会话列表合并账号归属"""

    def _register_with_session(self, client, session_id):
        resp = client.post("/api/auth/register", json={
            "email": "alice@qq.com", "password": "password123",
            "nickname": "小明", "session_id": session_id,
        })
        assert resp.status_code == 201
        return resp.json()

    def test_register_binds_current_session(self, client_with_auth):
        client, tracker, auth = client_with_auth
        body = self._register_with_session(client, "sess-browser-1")
        assert body["session_id"] == "sess-browser-1"
        assert [s["session_id"] for s in auth.list_user_sessions(1)] == ["sess-browser-1"]

    def test_login_on_new_device_returns_latest_session(self, client_with_auth, mock_engine):
        client, tracker, auth = client_with_auth
        self._register_with_session(client, "sess-old")
        client.post("/api/auth/logout")

        # 模拟旧设备继续学习产生新会话（attach 更新 last_active_at）
        auth.attach_session(1, "sess-new")

        # 新浏览器：TestClient 自动带新 aigc_visitor cookie（首次请求种下）
        visitor = client.cookies.get("aigc_visitor")
        resp = client.post("/api/auth/login", json={
            "email": "alice@qq.com", "password": "password123", "session_id": None,
        })
        assert resp.json()["session_id"] == "sess-new"
        # 会话改绑到当前浏览器访客 → 聊天历史/做题数据在新设备可见
        assert tracker.get_session_owner("sess-new") == visitor

    def test_sessions_list_merges_user_and_visitor(self, client_with_auth, mock_engine):
        client, tracker, auth = client_with_auth
        mock_engine.list_sessions.return_value = ["sess-mine", "sess-other-visitor", "sess-account-old"]
        mock_engine.get_session_history.return_value = [
            {"role": "user", "content": "什么是TCP?"},
            {"role": "assistant", "content": "…"},
        ]
        # 本访客拥有 sess-mine；账号另有 sess-account-old（在别的访客名下）
        client.get("/api/auth/me")  # 先发一次请求种下访客 cookie
        visitor = client.cookies.get("aigc_visitor")
        assert visitor
        tracker.bind_session("sess-mine", visitor)
        tracker.bind_session("sess-other-visitor", visitor)
        tracker.bind_session("sess-account-old", "other-visitor")

        self._register_with_session(client, "sess-mine")
        auth.attach_session(1, "sess-account-old")

        sessions = client.get("/api/sessions").json()["sessions"]
        ids = {s["session_id"]: s for s in sessions}
        assert set(ids) == {"sess-mine", "sess-other-visitor", "sess-account-old"}
        assert ids["sess-mine"]["user_owned"] is True
        assert ids["sess-account-old"]["user_owned"] is True
        assert ids["sess-other-visitor"]["user_owned"] is False

    def test_new_session_attaches_to_logged_in_user(self, client_with_auth, mock_engine):
        client, tracker, auth = client_with_auth
        self._register_with_session(client, "sess-initial")
        mock_engine.new_session.return_value = "sess-fresh"
        resp = client.post("/api/sessions/new")
        assert resp.json()["session_id"] == "sess-fresh"
        assert "sess-fresh" in [s["session_id"] for s in auth.list_user_sessions(1)]

    def test_claim_endpoint_switches_session(self, client_with_auth, mock_engine):
        client, tracker, auth = client_with_auth
        self._register_with_session(client, "sess-current")
        auth.attach_session(1, "sess-history")
        tracker.bind_session("sess-history", "old-device-visitor")

        resp = client.post("/api/auth/sessions/claim", json={"session_id": "sess-history"})
        assert resp.status_code == 200
        visitor = client.cookies.get("aigc_visitor")
        assert tracker.get_session_owner("sess-history") == visitor

    def test_claim_rejects_foreign_session(self, client_with_auth):
        client, tracker, auth = client_with_auth
        self._register_with_session(client, "sess-mine")
        auth.register("other@x.com", "password123", "他人")
        auth.attach_session(2, "sess-theirs")
        resp = client.post("/api/auth/sessions/claim", json={"session_id": "sess-theirs"})
        assert resp.status_code == 404

    def test_claim_requires_login(self, client_with_auth):
        client, _, _ = client_with_auth
        resp = client.post("/api/auth/sessions/claim", json={"session_id": "any"})
        assert resp.status_code == 401

    def test_delete_session_detaches_from_account(self, client_with_auth, mock_engine):
        client, tracker, auth = client_with_auth
        self._register_with_session(client, "sess-doomed")
        mock_engine.list_sessions.return_value = ["sess-doomed"]
        resp = client.delete("/api/sessions/sess-doomed")
        assert resp.json()["success"] is True
        assert auth.list_user_sessions(1) == []


@pytest.fixture
def client_with_roles(mock_engine, tmp_path):
    """带教师/管理员邀请码 + ADMIN_TOKEN 的环境：yield (client, tracker, auth)。"""
    import web.graph_rag_web as web_mod

    tracker = MasteryTracker(db_path=str(tmp_path / "mastery.db"))
    auth = AuthService(
        db_path=str(tmp_path / "mastery.db"), tracker=tracker,
        teacher_code="T-INVITE", admin_code="A-INVITE",
    )
    orig = {
        "engine": web_mod.deps.langgraph_engine,
        "mastery_service": web_mod.deps.mastery_service,
        "auth_service": web_mod.deps.auth_service,
        "trial_service": web_mod.deps.trial_service,
    }
    web_mod.deps.langgraph_engine = mock_engine
    web_mod.deps.mastery_service = MasteryService(tracker)
    web_mod.deps.auth_service = auth
    web_mod.deps.trial_service = MagicMock()

    with patch("web.middleware.get_settings") as mock_mw, \
         patch("web.deps.get_settings") as mock_deps:
        mock_mw.return_value = MagicMock(api_key="")
        mock_deps.return_value = MagicMock(admin_token="tok-123")
        c = TestClient(web_mod.app)
        yield c, tracker, auth, mock_deps

    web_mod.deps.langgraph_engine = orig["engine"]
    web_mod.deps.mastery_service = orig["mastery_service"]
    web_mod.deps.auth_service = orig["auth_service"]
    web_mod.deps.trial_service = orig["trial_service"]
    auth.close()
    tracker.close()


class TestRoleRegistration:
    def test_student_register_no_code_needed(self, client_with_roles):
        client, _, _, _ = client_with_roles
        resp = client.post("/api/auth/register", json={
            "email": "stu@x.com", "password": "password123", "nickname": "同学",
        })
        assert resp.status_code == 201
        assert resp.json()["user"]["role"] == "student"

    def test_teacher_requires_invite_code(self, client_with_roles):
        client, _, _, _ = client_with_roles
        for code in ["", "wrong"]:
            resp = client.post("/api/auth/register", json={
                "email": "t@x.com", "password": "password123", "nickname": "老师",
                "role": "teacher", "invite_code": code,
            })
            assert resp.status_code == 403

    def test_teacher_valid_code_registers_and_me_returns_role(self, client_with_roles):
        client, _, _, _ = client_with_roles
        resp = client.post("/api/auth/register", json={
            "email": "t@x.com", "password": "password123", "nickname": "老师",
            "role": "teacher", "invite_code": "T-INVITE",
        })
        assert resp.status_code == 201
        assert resp.json()["user"]["role"] == "teacher"
        me = client.get("/api/auth/me").json()
        assert me["user"]["role"] == "teacher"

    def test_admin_valid_code_registers(self, client_with_roles):
        client, _, _, _ = client_with_roles
        resp = client.post("/api/auth/register", json={
            "email": "adm@x.com", "password": "password123", "nickname": "管理员",
            "role": "admin", "invite_code": "A-INVITE",
        })
        assert resp.status_code == 201
        assert resp.json()["user"]["role"] == "admin"

    def test_invalid_role_422(self, client_with_roles):
        client, _, _, _ = client_with_roles
        resp = client.post("/api/auth/register", json={
            "email": "x@x.com", "password": "password123", "nickname": "坏",
            "role": "principal",
        })
        assert resp.status_code == 422


class TestRequireAdminDualChannel:
    """require_admin：admin 角色登录 OR X-Admin-Token 双通道。"""

    def _login(self, client, email):
        client.cookies.delete("aigc_user")
        resp = client.post("/api/auth/login", json={"email": email, "password": "password123"})
        assert resp.status_code == 200

    def _register_all(self, client):
        client.post("/api/auth/register", json={"email": "stu2@x.com", "password": "password123", "nickname": "学生"})
        client.post("/api/auth/register", json={"email": "tea2@x.com", "password": "password123", "nickname": "教师", "role": "teacher", "invite_code": "T-INVITE"})
        client.post("/api/auth/register", json={"email": "adm2@x.com", "password": "password123", "nickname": "管理", "role": "admin", "invite_code": "A-INVITE"})
        client.cookies.delete("aigc_user")

    def test_admin_role_passes_without_token(self, client_with_roles):
        client, _, _, _ = client_with_roles
        self._register_all(client)
        self._login(client, "adm2@x.com")
        assert client.get("/api/trial/list").status_code == 200

    def test_teacher_and_student_get_403(self, client_with_roles):
        client, _, _, _ = client_with_roles
        self._register_all(client)
        self._login(client, "tea2@x.com")
        assert client.get("/api/trial/list").status_code == 403
        self._login(client, "stu2@x.com")
        assert client.get("/api/trial/list").status_code == 403

    def test_guest_with_valid_token_passes(self, client_with_roles):
        client, _, _, _ = client_with_roles
        client.cookies.delete("aigc_user")
        resp = client.get("/api/trial/list", headers={"X-Admin-Token": "tok-123"})
        assert resp.status_code == 200

    def test_guest_without_credentials_401(self, client_with_roles):
        client, _, _, _ = client_with_roles
        resp = client.get("/api/trial/list")
        assert resp.status_code == 401
        resp = client.get("/api/trial/list", headers={"X-Admin-Token": "bad"})
        assert resp.status_code == 401

    def test_logged_in_student_with_valid_token_still_passes(self, client_with_roles):
        client, _, _, _ = client_with_roles
        self._register_all(client)
        self._login(client, "stu2@x.com")
        resp = client.get("/api/trial/list", headers={"X-Admin-Token": "tok-123"})
        assert resp.status_code == 200

    def test_admin_token_unconfigured_admin_role_still_works(self, client_with_roles):
        client, _, _, mock_deps = client_with_roles
        mock_deps.return_value = MagicMock(admin_token="")  # fail-closed token 通道
        client, _, _, _ = client_with_roles
        self._register_all(client)
        self._login(client, "adm2@x.com")
        assert client.get("/api/trial/list").status_code == 200  # 角色通道不受影响
        client.cookies.delete("aigc_user")
        assert client.get("/api/trial/list", headers={"X-Admin-Token": "any"}).status_code == 401
