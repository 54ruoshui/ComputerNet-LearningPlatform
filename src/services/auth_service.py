"""
账号认证服务 — 邮箱+密码注册登录（可选登录，叠加在访客 cookie 机制之上）

- 密码哈希：stdlib hashlib.scrypt（零新依赖），参数随哈希串存储、验证时解析（可升级）
- 登录态：服务端随机 token，DB 只存 sha256（DB 泄露不可伪造凭证），HttpOnly cookie 下发
- 账号↔会话绑定：user_sessions 表记录账号终身归属（先绑先得）；
  session_owners（浏览器当前归属）由 MasteryTracker 的 bind/claim 原语管理，两表平行独立
- 锁纪律：self._lock 只保护 auth 自己的连接操作；调用 tracker 方法时不得持有本锁（防死锁）
"""

import base64
import hashlib
import hmac
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import structlog

logger = structlog.get_logger(__name__)

SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_SALT_BYTES = 16
# OpenSSL 默认 maxmem (~32MB) 装不下 n=2^17；显式放宽到 64MB（n=2^14 实际占用 ~16MB）
SCRYPT_MAXMEM = 64 * 1024 * 1024

DEFAULT_TOKEN_DAYS = 30
DEFAULT_MAX_FAILURES = 5
DEFAULT_LOCKOUT_MINUTES = 10


def _utcnow() -> str:
    """ISO UTC 时间戳单一出口（测试 monkeypatch 用）；ISO 字符串序与时间序一致"""
    return datetime.now(timezone.utc).isoformat()


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(SCRYPT_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
        maxmem=SCRYPT_MAXMEM,
    )
    return "$".join([
        "scrypt",
        str(SCRYPT_N),
        str(SCRYPT_R),
        str(SCRYPT_P),
        base64.urlsafe_b64encode(salt).decode(),
        base64.urlsafe_b64encode(digest).decode(),
    ])


def _verify_password(password: str, stored: str) -> bool:
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt = base64.urlsafe_b64decode(parts[4])
        expected = base64.urlsafe_b64decode(parts[5])
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
            maxmem=SCRYPT_MAXMEM,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


# 登录时用户不存在也跑一次同成本验证，拉平响应耗时（防时序探测）
_DUMMY_HASH = _hash_password(secrets.token_urlsafe(16))


class AuthError(Exception):
    """认证业务错误基类"""


class EmailExistsError(AuthError):
    """邮箱已注册（注册场景需要明确提示，接受用户枚举）"""


class InvalidCredentialsError(AuthError):
    """邮箱或密码错误（统一文案，不区分用户不存在/密码错）"""


class AccountLockedError(AuthError):
    """连续登录失败触发锁定"""

    def __init__(self, retry_seconds: int):
        super().__init__(f"尝试次数过多，账号已锁定，请 {max(retry_seconds, 1) // 60 + 1} 分钟后再试")
        self.retry_seconds = retry_seconds


class AuthService:
    def __init__(
        self,
        db_path: str,
        tracker=None,
        token_days: int = DEFAULT_TOKEN_DAYS,
        max_failures: int = DEFAULT_MAX_FAILURES,
        lockout_minutes: int = DEFAULT_LOCKOUT_MINUTES,
    ):
        self.db_path = db_path
        self._tracker = tracker
        self._token_days = token_days
        self._max_failures = max_failures
        self._lockout_minutes = lockout_minutes
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self._init_tables()

    @staticmethod
    def hash_password(password: str) -> str:
        return _hash_password(password)

    @staticmethod
    def verify_password(password: str, stored: str) -> bool:
        return _verify_password(password, stored)

    def _init_tables(self):
        with self._lock:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    email            TEXT NOT NULL UNIQUE,
                    nickname         TEXT NOT NULL,
                    password_hash    TEXT NOT NULL,
                    failed_attempts  INTEGER NOT NULL DEFAULT 0,
                    locked_until     TEXT,
                    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS auth_tokens (
                    token_hash   TEXT PRIMARY KEY,
                    user_id      INTEGER NOT NULL REFERENCES users(id),
                    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                    expires_at   TEXT NOT NULL,
                    last_used_at TEXT
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_auth_tokens_user ON auth_tokens(user_id)"
            )
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS user_sessions (
                    session_id     TEXT PRIMARY KEY,
                    user_id        INTEGER NOT NULL REFERENCES users(id),
                    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
                    last_active_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            self.conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_sessions_user
                ON user_sessions(user_id, last_active_at DESC)
            """)
            self.conn.commit()

    # ==================== 注册 / 登录 / 登出 ====================

    def register(self, email: str, password: str, nickname: str) -> dict:
        email = email.strip().lower()
        nickname = nickname.strip()
        with self._lock:
            existing = self.conn.execute(
                "SELECT id FROM users WHERE email = ?", (email,)
            ).fetchone()
            if existing:
                raise EmailExistsError("该邮箱已注册，请直接登录")
            cur = self.conn.execute(
                "INSERT INTO users (email, nickname, password_hash) VALUES (?, ?, ?)",
                (email, nickname, _hash_password(password)),
            )
            self.conn.commit()
            user_id = cur.lastrowid
        row = self.conn.execute(
            "SELECT id, email, nickname, created_at FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return {"id": row[0], "email": row[1], "nickname": row[2], "created_at": row[3]}

    def login(
        self,
        email: str,
        password: str,
        visitor_id: Optional[str] = None,
        current_session_id: Optional[str] = None,
    ) -> dict:
        """密码校验 + 发 token + 把当前会话 attach 到账号 + claim 账号最新会话到当前访客。

        返回 {user, token, session_id}；session_id 为接续会话（账号+当前浏览器都无会话时为 None）。
        """
        email = email.strip().lower()
        now = _utcnow()
        with self._lock:
            row = self.conn.execute(
                "SELECT id, email, nickname, password_hash, failed_attempts, locked_until FROM users WHERE email = ?",
                (email,),
            ).fetchone()
        if row is None:
            _verify_password(password, _DUMMY_HASH)
            raise InvalidCredentialsError("邮箱或密码错误")
        user_id, _, nickname, password_hash, failed_attempts, locked_until = row

        if locked_until and locked_until > now:
            retry = datetime.fromisoformat(locked_until) - datetime.fromisoformat(now)
            raise AccountLockedError(max(int(retry.total_seconds()), 1))

        if not _verify_password(password, password_hash):
            with self._lock:
                attempts = failed_attempts + 1
                if attempts >= self._max_failures:
                    locked_until_iso = (
                        datetime.fromisoformat(now) + timedelta(minutes=self._lockout_minutes)
                    ).isoformat()
                    self.conn.execute(
                        "UPDATE users SET failed_attempts = 0, locked_until = ? WHERE id = ?",
                        (locked_until_iso, user_id),
                    )
                else:
                    self.conn.execute(
                        "UPDATE users SET failed_attempts = ? WHERE id = ?",
                        (attempts, user_id),
                    )
                self.conn.commit()
            raise InvalidCredentialsError("邮箱或密码错误")

        with self._lock:
            self.conn.execute(
                "UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE id = ?",
                (user_id,),
            )
            self.conn.commit()

        token = self._issue_token(user_id)

        # 当前浏览器会话先绑先得地 attach 到账号（已被他人绑定则跳过，视同无当前会话）
        if current_session_id and current_session_id != "default":
            self.attach_session(user_id, current_session_id)

        latest = self._latest_session_for_user(user_id)
        if latest:
            self._claim_to_visitor(latest, visitor_id)
        return {
            "user": {"id": user_id, "email": email, "nickname": nickname},
            "token": token,
            "session_id": latest,
        }

    def logout(self, token: str) -> None:
        if not token:
            return
        with self._lock:
            self.conn.execute(
                "DELETE FROM auth_tokens WHERE token_hash = ?",
                (hashlib.sha256(token.encode("utf-8")).hexdigest(),),
            )
            self.conn.commit()

    def resolve_user(self, token: Optional[str]) -> Optional[dict]:
        """token → 用户信息；无效/过期/异常一律 None（auth 是可选叠加层）"""
        if not token:
            return None
        try:
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            with self._lock:
                row = self.conn.execute(
                    """SELECT u.id, u.email, u.nickname, t.expires_at
                       FROM auth_tokens t JOIN users u ON u.id = t.user_id
                       WHERE t.token_hash = ?""",
                    (token_hash,),
                ).fetchone()
                if row is None or row[3] <= _utcnow():
                    return None
                self.conn.execute(
                    "UPDATE auth_tokens SET last_used_at = ? WHERE token_hash = ?",
                    (_utcnow(), token_hash),
                )
                self.conn.commit()
            return {"id": row[0], "email": row[1], "nickname": row[2]}
        except Exception as e:
            logger.warning("解析登录态失败", error=str(e))
            return None

    # ==================== 账号↔会话绑定 ====================

    def list_user_sessions(self, user_id: int) -> List[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT session_id, last_active_at FROM user_sessions WHERE user_id = ? ORDER BY last_active_at DESC",
                (user_id,),
            ).fetchall()
        return [{"session_id": r[0], "last_active_at": r[1]} for r in rows]

    def attach_session(self, user_id: int, session_id: str) -> bool:
        """把会话记入账号（先绑先得）；返回会话是否归属该用户"""
        now = _utcnow()
        with self._lock:
            row = self.conn.execute(
                "SELECT user_id FROM user_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if row is None:
                self.conn.execute(
                    "INSERT INTO user_sessions (session_id, user_id, last_active_at) VALUES (?, ?, ?)",
                    (session_id, user_id, now),
                )
                self.conn.commit()
                return True
            if row[0] != user_id:
                return False
            self.conn.execute(
                "UPDATE user_sessions SET last_active_at = ? WHERE session_id = ?",
                (now, session_id),
            )
            self.conn.commit()
            return True

    def claim_user_session(self, user_id: int, session_id: str, visitor_id: Optional[str]) -> bool:
        """显式切换会话：校验账号归属后把会话改绑到当前访客（不持 auth 锁调 tracker）"""
        with self._lock:
            row = self.conn.execute(
                "SELECT user_id FROM user_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if row is None or row[0] != user_id:
                return False
            self.conn.execute(
                "UPDATE user_sessions SET last_active_at = ? WHERE session_id = ?",
                (_utcnow(), session_id),
            )
            self.conn.commit()
        self._claim_to_visitor(session_id, visitor_id)
        return True

    def detach_session(self, session_id: str) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM user_sessions WHERE session_id = ?", (session_id,))
            self.conn.commit()

    # ==================== 内部 ====================

    def _issue_token(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        expires_at = (
            datetime.fromisoformat(_utcnow()) + timedelta(days=self._token_days)
        ).isoformat()
        with self._lock:
            # 登录路径顺带惰性 GC 过期 token，无需后台线程
            self.conn.execute("DELETE FROM auth_tokens WHERE expires_at < ?", (_utcnow(),))
            self.conn.execute(
                "INSERT INTO auth_tokens (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (hashlib.sha256(token.encode("utf-8")).hexdigest(), user_id, expires_at),
            )
            self.conn.commit()
        return token

    def _latest_session_for_user(self, user_id: int) -> Optional[str]:
        with self._lock:
            row = self.conn.execute(
                "SELECT session_id FROM user_sessions WHERE user_id = ? ORDER BY last_active_at DESC LIMIT 1",
                (user_id,),
            ).fetchone()
        return row[0] if row else None

    def _claim_to_visitor(self, session_id: str, visitor_id: Optional[str]):
        if not self._tracker or not visitor_id:
            logger.warning("tracker/访客不可用，跳过会话改绑", session_id=session_id)
            return
        try:
            self._tracker.claim_session(session_id, visitor_id)
        except Exception as e:
            logger.warning("会话改绑失败", session_id=session_id, error=str(e))

    def cleanup_expired_tokens(self) -> int:
        with self._lock:
            cur = self.conn.execute("DELETE FROM auth_tokens WHERE expires_at < ?", (_utcnow(),))
            self.conn.commit()
        return cur.rowcount

    def close(self):
        if self.conn:
            self.conn.close()
            logger.info("AuthService 数据库连接已关闭")
