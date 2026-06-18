"""
轻量级 IP 速率限制中间件
基于滑动窗口，按端点分级限制，零外部依赖。
"""

import time
import threading
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

# 端点限流配置: {路径前缀: (窗口秒数, 最大请求数)}
ENDPOINT_LIMITS = {
    "/api/query":     (60, 10),   # 查询最耗资源
    "/api/quiz/answer": (10, 5),  # 答题短窗口
    "/api/generate_image": (60, 5),
}

DEFAULT_LIMIT = (30, 30)  # 其他 API: 30秒内 30次
CLEANUP_INTERVAL = 120


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
        max_window = max(w for _, (w, _) in ENDPOINT_LIMITS.values()) + DEFAULT_LIMIT[0]
        cutoff = now - max_window
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


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path

        # 匹配限流规则
        window, max_req = DEFAULT_LIMIT
        for prefix, limits in ENDPOINT_LIMITS.items():
            if path.startswith(prefix):
                window, max_req = limits
                break

        client_ip = request.client.host if request.client else "unknown"
        key = f"{client_ip}:{path}"

        if not _rate_limiter.is_allowed(key, window, max_req):
            return JSONResponse(
                status_code=429,
                content={
                    "detail": f"请求过于频繁，请 {window} 秒后再试",
                    "retry_after": window,
                },
            )

        return await call_next(request)
