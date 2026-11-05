"""
双桶速率限制中间件（纯 ASGI 实现，避免 BaseHTTPMiddleware 与 StaticFiles 的兼容性问题）。

只对 /api/* 限流（静态资源与页面路由直接放行，避免班级同时开页即集体 429）。
- IP 桶：按访客 IP 的粗天花板（班级 NAT 场景下全班共享出口 IP）
- 会话桶：按 session_id 的公平配额（能从请求中提取到 session_id 时叠加生效）
两桶任一超限即 429。零外部依赖。
"""

import json
import time
import threading
from collections import defaultdict
from urllib.parse import parse_qsl

from starlette.responses import JSONResponse

# IP 桶配置: {路径前缀: (窗口秒数, 最大请求数)}
# 数值按“一个班 ~50 人共享 NAT 出口”的天花板设定
IP_LIMITS = {
    "/api/query":          (60, 150),   # 查询最耗资源（含 /api/query/stream）
    "/api/quiz/answer":    (60, 1800),  # 答题轻量
    "/api/quiz/generate":  (60, 30),    # LLM 出题（管理端，收紧）
    "/api/generate_image": (60, 30),
    "/api/trial/report":   (60, 30),    # LLM 生成评估报告（管理端，收紧）
    "/api/trial/join":     (60, 60),    # 被试码端点防枚举（班级 NAT 天花板与登录一致）
    "/api/trial/test/start": (60, 60),  # 同上：凭被试码调用
    "/api/auth/login":     (60, 60),    # 登录防爆破下限（主力是每账号失败锁定，NAT 下 IP 桶不能太紧）
    "/api/auth/register":  (60, 60),
}
IP_DEFAULT_LIMIT = (60, 1800)

# 会话桶配置：能提取 session_id 时叠加检查（防止单人打满 IP 天花板挤占他人）
SESSION_LIMITS = {
    "/api/query":       (60, 10),
    "/api/quiz/answer": (60, 30),
}
SESSION_DEFAULT_LIMIT = (60, 60)

# 需要读 body 提取 session_id 的 POST 路径前缀（读后重放，路由无感知）
BODY_SNIFF_PREFIXES = ("/api/query", "/api/quiz/")
MAX_SNIFF_BODY = 64 * 1024  # 超过此大小的 body 不读取，仅走 IP 桶

CLEANUP_INTERVAL = 120


def _match_limit(table: dict, default: tuple, path: str) -> tuple:
    for prefix, limits in table.items():
        if path.startswith(prefix):
            return limits
    return default


class RateLimiter:
    def __init__(self):
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def is_allowed(self, key: str, window: int, max_req: int) -> bool:
        now = time.time()
        with self._lock:
            bucket = self._buckets[key]
            # 清理过期记录
            cutoff = now - window
            while bucket and bucket[0] < cutoff:
                bucket.pop(0)
            if len(bucket) >= max_req:
                return False
            bucket.append(now)
            return True

    def cleanup(self):
        """后台清理超过最大窗口的过期 key"""
        now = time.time()
        windows = [w for (w, _) in IP_LIMITS.values()] + [IP_DEFAULT_LIMIT[0]]
        windows += [w for (w, _) in SESSION_LIMITS.values()] + [SESSION_DEFAULT_LIMIT[0]]
        cutoff = now - max(windows)
        with self._lock:
            stale = [k for k, v in self._buckets.items() if not v or v[-1] < cutoff]
            for k in stale:
                del self._buckets[k]


_rate_limiter = RateLimiter()


def _start_cleanup_thread():
    def _run():
        while True:
            time.sleep(CLEANUP_INTERVAL)
            _rate_limiter.cleanup()

    t = threading.Thread(target=_run, daemon=True)
    t.start()


_start_cleanup_thread()


def _session_from_query(scope) -> str | None:
    qs = scope.get("query_string", b"").decode("latin-1")
    for name, value in parse_qsl(qs):
        if name == "session_id" and value:
            return value
    return None


def _content_length(scope) -> int | None:
    for key, value in scope.get("headers", []):
        if key == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _sniff_session_and_replay(scope, receive):
    """读取（限定路径与大小的）请求体提取 session_id，返回 (session_id, 重放 receive)。

    - 读到的 body 必须以重放 receive 交还，否则路由将读到空 body
    - 非 JSON / 无 session_id / 连接中断 → session_id 为 None，body 仍尽力重放
    """
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] == "http.request":
            body = message.get("body", b"")
            if body:
                chunks.append(body)
            if not message.get("more_body", False):
                break
        elif message["type"] == "http.disconnect":
            # 连接已断：不再等待剩余 body，有什么重放什么
            break

    body = b"".join(chunks)

    session_id = None
    if body:
        try:
            payload = json.loads(body)
            if isinstance(payload, dict):
                sid = payload.get("session_id")
                if isinstance(sid, str) and sid:
                    session_id = sid
        except (ValueError, UnicodeDecodeError):
            pass

    # 先交还完整 body（单条 full-body 消息），之后透传真实 receive：
    # 伪造 http.disconnect 会被 StreamingResponse 的断连监听当成客户端断开，
    # 立即取消流式生成器（SSE 空流），故必须保留真实断连语义
    pending = [{"type": "http.request", "body": body, "more_body": False}]

    async def replay():
        if pending:
            return pending.pop(0)
        return await receive()

    return session_id, replay


class RateLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        if not path.startswith("/api/"):
            # 静态资源与页面路由不限流（班级同时开页是正常负载）
            await self.app(scope, receive, send)
            return

        # ---- IP 桶（班级 NAT 天花板） ----
        ip_window, ip_max = _match_limit(IP_LIMITS, IP_DEFAULT_LIMIT, path)
        client = scope.get("client")
        client_ip = client[0] if client else "unknown"

        # ---- IP 桶（班级 NAT 天花板） ----
        if not _rate_limiter.is_allowed(f"ip:{client_ip}:{path}", ip_window, ip_max):
            await self._send_429(scope, receive, send, ip_window)
            return

        # ---- 会话桶（可提取 session_id 时叠加） ----
        session_id = _session_from_query(scope)
        replay_receive = None
        if (
            session_id is None
            and scope.get("method") == "POST"
            and path.startswith(BODY_SNIFF_PREFIXES)
        ):
            length = _content_length(scope)
            if length is not None and 0 < length <= MAX_SNIFF_BODY:
                session_id, replay_receive = await _sniff_session_and_replay(scope, receive)

        if session_id:
            sess_window, sess_max = _match_limit(SESSION_LIMITS, SESSION_DEFAULT_LIMIT, path)
            if not _rate_limiter.is_allowed(f"sess:{session_id}:{path}", sess_window, sess_max):
                await self._send_429(scope, receive, send, sess_window)
                return

        # body 已被读取过则用重放 receive 交还
        await self.app(scope, replay_receive or receive, send)

    @staticmethod
    async def _send_429(scope, receive, send, window: int):
        response = JSONResponse(
            status_code=429,
            content={
                "detail": f"请求过于频繁，请 {window} 秒后再试",
                "retry_after": window,
            },
            headers={"Retry-After": str(window)},
        )
        await response(scope, receive, send)
