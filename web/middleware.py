"""
自定义中间件（纯 ASGI 实现，避免 BaseHTTPMiddleware 与 StaticFiles 的兼容性问题）。
- APIKeyMiddleware: X-API-Key 认证
- RequestIDMiddleware: 为每个请求注入 X-Request-ID
"""

from starlette.responses import JSONResponse

from src.logging_config import new_request_id
from src.settings import get_settings

# 白名单路径（不需要认证）
PUBLIC_PATHS = {"/", "/quiz", "/review", "/docs", "/openapi.json", "/redoc", "/api/health"}


class APIKeyMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        api_key = get_settings().api_key
        # 未配置 API_KEY → 跳过认证
        if not api_key:
            await self.app(scope, receive, send)
            return

        path = scope["path"]

        # 白名单路径放行
        if path in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        # 静态文件放行
        if path.startswith("/static/") or path.startswith("/generated_images/"):
            await self.app(scope, receive, send)
            return

        # 校验 API Key（从 scope["headers"] 读取，无需消费请求体）
        headers = dict(scope.get("headers", []))
        request_key = headers.get(b"x-api-key", b"").decode()

        if request_key != api_key:
            response = JSONResponse(
                status_code=401,
                content={"detail": "无效或缺失 API Key，请在请求头中设置 X-API-Key"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


class RequestIDMiddleware:
    """为每个 HTTP 请求注入 X-Request-ID 响应头，并设置日志上下文。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        rid = new_request_id()

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", rid.encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_id)
