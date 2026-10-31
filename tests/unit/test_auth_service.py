"""AuthService 单元测试 — scrypt 哈希、注册登录、锁定、账号↔会话绑定（真 SQLite + 真 tracker）"""

import pytest

from src.mastery_tracker import MasteryTracker
from src.services.auth_service import (
    AccountLockedError,
    EmailExistsError,
    InvalidCredentialsError,
    AuthService,
    _hash_password,
    _verify_password,
)


@pytest.fixture
def tracker(tmp_path):
    t = MasteryTracker(db_path=str(tmp_path / "mastery.db"))
    yield t
    t.close()


@pytest.fixture
def auth(tmp_path, tracker):
    svc = AuthService(
        db_path=str(tmp_path / "mastery.db"),
        tracker=tracker,
        token_days=30,
        max_failures=3,
        lockout_minutes=10,
    )
    yield svc
    svc.close()


class TestPasswordHash:
    def test_roundtrip_and_rejects_wrong(self):
        stored = _hash_password("s3cret-pass")
        assert _verify_password("s3cret-pass", stored)
        assert not _verify_password("wrong-pass", stored)

    def test_format_encodes_params_and_unique_salt(self):
        a = _hash_password("pw")
        b = _hash_password("pw")
        parts = a.split("$")
        assert parts[0] == "scrypt" and len(parts) == 6
        assert parts[1:] != b.split("$")[1:]  # 盐随机

    def test_verify_malformed_stored_returns_false(self):
        assert not _verify_password("pw", "garbage")
        assert not _verify_password("pw", "scrypt$1$2$3$xxx$yyy")


class TestRegister:
    def test_creates_user_with_normalized_email(self, auth):
        user = auth.register("  Alice@QQ.COM ", "password123", "  小明  ")
        assert user["email"] == "alice@qq.com"
        assert user["nickname"] == "小明"
        assert user["id"] and user["created_at"]

    def test_duplicate_email_raises(self, auth):
        auth.register("a@b.com", "password123", "甲")
        with pytest.raises(EmailExistsError):
            auth.register("A@b.com", "password456", "乙")


class TestLogin:
    def test_success_returns_token_user_and_session(self, auth, tracker):
        auth.register("a@b.com", "password123", "小明")
        tracker.bind_session("sess-1", "visitor-old")
        result = auth.login("a@b.com", "password123", visitor_id="visitor-new", current_session_id="sess-1")
        assert result["user"]["nickname"] == "小明"
        assert result["token"]
        assert result["session_id"] == "sess-1"
        # 会话被 claim 到当前访客（跨设备找回的核心断言）
        assert tracker.get_session_owner("sess-1") == "visitor-new"

    def test_wrong_password_and_unknown_email_same_error(self, auth):
        auth.register("a@b.com", "password123", "小明")
        with pytest.raises(InvalidCredentialsError):
            auth.login("a@b.com", "wrong-password")
        with pytest.raises(InvalidCredentialsError):
            auth.login("ghost@b.com", "password123")

    def test_lockout_after_max_failures_then_expires(self, auth, monkeypatch):
        from src.services import auth_service as mod
        auth.register("a@b.com", "password123", "小明")
        base = mod._utcnow()
        for _ in range(3):
            with pytest.raises(InvalidCredentialsError):
                auth.login("a@b.com", "wrong-password")
        with pytest.raises(AccountLockedError) as exc:
            auth.login("a@b.com", "password123")
        assert exc.value.retry_seconds > 0
        # 时间前进 11 分钟，锁定过期，正确密码可登录
        fake_now = mod.datetime.fromisoformat(base) + mod.timedelta(minutes=11)
        monkeypatch.setattr(mod, "_utcnow", lambda: fake_now.isoformat())
        result = auth.login("a@b.com", "password123")
        assert result["token"]

    def test_success_resets_failure_counter(self, auth):
        auth.register("a@b.com", "password123", "小明")
        for _ in range(2):  # max_failures=3，先错 2 次
            with pytest.raises(InvalidCredentialsError):
                auth.login("a@b.com", "wrong-password")
        auth.login("a@b.com", "password123")
        for _ in range(2):  # 计数已清零，再错 2 次不应触发锁定（第 3 次才锁）
            with pytest.raises(InvalidCredentialsError):
                auth.login("a@b.com", "wrong-password")
        result = auth.login("a@b.com", "password123")
        assert result["token"]

    def test_attaches_current_session_first_wins(self, auth):
        auth.register("a@b.com", "password123", "甲")
        auth.register("c@d.com", "password123", "乙")
        assert auth.attach_session(1, "sess-x") is True
        assert auth.attach_session(2, "sess-x") is False  # 他人先绑
        assert auth.attach_session(1, "sess-x") is True   # 本人重复 attach 仍 True 并 touch

    def test_login_skips_session_owned_by_other_user(self, auth, tracker):
        auth.register("a@b.com", "password123", "甲")
        auth.register("c@d.com", "password123", "乙")
        auth.attach_session(1, "sess-owner")  # 甲先绑
        result = auth.login("c@d.com", "password123", visitor_id="v2", current_session_id="sess-owner")
        # 乙的当前会话被甲占用 → 跳过 attach，账号又无其他会话 → session_id 为 None
        assert result["session_id"] is None

    def test_login_cross_device_returns_account_latest_session(self, auth, tracker):
        auth.register("a@b.com", "password123", "甲")
        auth.attach_session(1, "sess-old")
        # 模拟旧设备再次活跃使 sess-old 更新（attach 本身 touch）
        auth.attach_session(1, "sess-new")
        result = auth.login("a@b.com", "password123", visitor_id="v-new", current_session_id=None)
        assert result["session_id"] == "sess-new"
        assert tracker.get_session_owner("sess-new") == "v-new"

    def test_login_without_tracker_still_succeeds(self, tmp_path):
        svc = AuthService(db_path=str(tmp_path / "auth.db"), tracker=None)
        try:
            svc.register("a@b.com", "password123", "甲")
            svc.attach_session(1, "sess-1")
            result = svc.login("a@b.com", "password123", visitor_id=None, current_session_id="sess-1")
            assert result["session_id"] == "sess-1"  # attach 生效，只是 claim 降级跳过
        finally:
            svc.close()

    def test_default_session_not_attached(self, auth):
        auth.register("a@b.com", "password123", "甲")
        auth.login("a@b.com", "password123", current_session_id="default")
        assert auth.list_user_sessions(1) == []


class TestTokens:
    def test_resolve_logout_and_expiry(self, auth, monkeypatch):
        from src.services import auth_service as mod
        auth.register("a@b.com", "password123", "甲")
        token = auth.login("a@b.com", "password123")["token"]
        assert auth.resolve_user(token)["email"] == "a@b.com"
        assert auth.resolve_user(None) is None
        assert auth.resolve_user("forged-token") is None

        # 时间前进 31 天 → token 过期
        base = mod._utcnow()
        fake_now = mod.datetime.fromisoformat(base) + mod.timedelta(days=31)
        monkeypatch.setattr(mod, "_utcnow", lambda: fake_now.isoformat())
        assert auth.resolve_user(token) is None

        # 重新登录拿新 token 后登出，resolve 变 None
        monkeypatch.setattr(mod, "_utcnow", lambda: base)
        token2 = auth.login("a@b.com", "password123")["token"]
        auth.logout(token2)
        assert auth.resolve_user(token2) is None

    def test_cleanup_expired_tokens(self, auth, monkeypatch):
        from src.services import auth_service as mod
        auth.register("a@b.com", "password123", "甲")
        base = mod._utcnow()
        auth.login("a@b.com", "password123")
        fake_now = mod.datetime.fromisoformat(base) + mod.timedelta(days=31)
        monkeypatch.setattr(mod, "_utcnow", lambda: fake_now.isoformat())
        assert auth.cleanup_expired_tokens() == 1


class TestSessionBinding:
    def test_list_user_sessions_ordered(self, auth):
        auth.register("a@b.com", "password123", "甲")
        auth.attach_session(1, "sess-a")
        auth.attach_session(1, "sess-b")
        sessions = auth.list_user_sessions(1)
        assert [s["session_id"] for s in sessions] == ["sess-b", "sess-a"]

    def test_claim_rejects_foreign_session(self, auth, tracker):
        auth.register("a@b.com", "password123", "甲")
        auth.register("c@d.com", "password123", "乙")
        auth.attach_session(1, "sess-mine")
        assert auth.claim_user_session(2, "sess-mine", "v-2") is False
        assert tracker.get_session_owner("sess-mine") is None  # 未改绑
        assert auth.claim_user_session(1, "sess-mine", "v-1") is True
        assert tracker.get_session_owner("sess-mine") == "v-1"

    def test_detach_removes_mapping(self, auth):
        auth.register("a@b.com", "password123", "甲")
        auth.attach_session(1, "sess-x")
        auth.detach_session("sess-x")
        assert auth.list_user_sessions(1) == []

    def test_hash_password_static_delegates(self):
        assert AuthService.hash_password("pw") != ""
        assert AuthService.verify_password("pw", _hash_password("pw"))
