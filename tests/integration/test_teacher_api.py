"""教师端班级学情 API 集成测试 — 角色守卫（401/403/200）+ 数据聚合（真 tracker + 真 AuthService）"""

import pytest
from starlette.testclient import TestClient
from unittest.mock import MagicMock, patch

from src.mastery_tracker import MasteryTracker
from src.services.auth_service import AuthService
from src.services.mastery_service import MasteryService

from tests.integration.test_api import mock_engine  # noqa: F401


@pytest.fixture
def client_teacher(mock_engine, tmp_path):
    tracker = MasteryTracker(db_path=str(tmp_path / "mastery.db"))
    auth = AuthService(
        db_path=str(tmp_path / "mastery.db"), tracker=tracker,
        teacher_code="T-INVITE", admin_code="A-INVITE",
    )
    auth.register("stu@x.com", "password123", "同学")
    auth.register("tea@x.com", "password123", "老师", role="teacher", invite_code="T-INVITE")
    auth.register("adm@x.com", "password123", "管理", role="admin", invite_code="A-INVITE")

    # 学生学习数据：注册学生 1 个会话 + 游客 1 个会话
    auth.attach_session(1, "sess-reg")
    tracker.mark_quiz_done("sess-reg", "q1")
    tracker.set_mastery("sess-reg", "TCP", True)
    tracker.bind_session("sess-guest", "v-guest")
    tracker.mark_quiz_done("sess-guest", "q1")
    tracker.mark_quiz_done("sess-guest", "q2")

    mock_dash = MagicMock()
    mock_dash.get_overview.return_value = {"mastery": {"total": 1}, "session_id": "sess-reg"}
    mock_dash.generate_report.return_value = "# 学习报告"

    import web.graph_rag_web as web_mod
    orig = {
        "engine": web_mod.deps.langgraph_engine,
        "mastery_service": web_mod.deps.mastery_service,
        "auth_service": web_mod.deps.auth_service,
        "dashboard_service": web_mod.deps.dashboard_service,
    }
    web_mod.deps.langgraph_engine = mock_engine
    web_mod.deps.mastery_service = MasteryService(tracker)
    web_mod.deps.auth_service = auth
    web_mod.deps.dashboard_service = mock_dash

    with patch("web.middleware.get_settings") as mock_mw:
        mock_mw.return_value = MagicMock(api_key="")
        c = TestClient(web_mod.app)
        yield c, tracker, auth, mock_dash

    web_mod.deps.langgraph_engine = orig["engine"]
    web_mod.deps.mastery_service = orig["mastery_service"]
    web_mod.deps.auth_service = orig["auth_service"]
    web_mod.deps.dashboard_service = orig["dashboard_service"]
    auth.close()
    tracker.close()


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    from web.rate_limiter import _rate_limiter
    _rate_limiter._buckets.clear()
    yield
    _rate_limiter._buckets.clear()


def _login(client, email):
    client.cookies.delete("aigc_user")
    resp = client.post("/api/auth/login", json={"email": email, "password": "password123"})
    assert resp.status_code == 200


class TestTeacherStudents:
    def test_guest_401(self, client_teacher):
        client, _, _, _ = client_teacher
        client.cookies.delete("aigc_user")
        assert client.get("/api/teacher/students").status_code == 401

    def test_student_403(self, client_teacher):
        client, _, _, _ = client_teacher
        _login(client, "stu@x.com")
        assert client.get("/api/teacher/students").status_code == 403

    def test_teacher_gets_full_class(self, client_teacher):
        client, tracker, auth, _ = client_teacher
        _login(client, "tea@x.com")
        resp = client.get("/api/teacher/students")
        assert resp.status_code == 200
        students = resp.json()["students"]
        ids = {s["session_id"] for s in students}
        assert ids == {"sess-reg", "sess-guest"}
        reg = next(s for s in students if s["session_id"] == "sess-reg")
        assert reg["nickname"] == "同学"
        guest = next(s for s in students if s["session_id"] == "sess-guest")
        assert guest["nickname"] == "游客" and guest["done_count"] == 2

    def test_admin_also_allowed(self, client_teacher):
        client, _, _, _ = client_teacher
        _login(client, "adm@x.com")
        assert client.get("/api/teacher/students").status_code == 200

    def test_overview_drilldown(self, client_teacher):
        client, _, _, mock_dash = client_teacher
        _login(client, "tea@x.com")
        resp = client.get("/api/teacher/students/sess-reg/overview")
        assert resp.status_code == 200
        assert resp.json()["mastery"]["total"] == 1
        mock_dash.get_overview.assert_called_once_with("sess-reg")

    def test_report_drilldown(self, client_teacher):
        client, _, _, mock_dash = client_teacher
        _login(client, "tea@x.com")
        resp = client.post("/api/teacher/students/sess-reg/report")
        assert resp.status_code == 200
        assert resp.json()["report"] == "# 学习报告"
        mock_dash.generate_report.assert_called_once_with("sess-reg")

    def test_student_cannot_drilldown_others(self, client_teacher):
        client, _, _, _ = client_teacher
        _login(client, "stu@x.com")
        assert client.get("/api/teacher/students/sess-reg/overview").status_code == 403
        assert client.post("/api/teacher/students/sess-reg/report").status_code == 403
