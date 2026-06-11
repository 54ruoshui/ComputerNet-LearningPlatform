"""
API Key 认证中间件
通过 X-API-Key 请求头校验，不设 API_KEY 环境变量则跳过认证（开发模式）。
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.settings import get_settings

# 白名单路径（不需要认证）
PUBLIC_PATHS = {"/", "/quiz", "/docs", "/openapi.json", "/redoc", "/api/health"}


class APIKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        api_key = get_settings().api_key
        # 未配置 API_KEY → 跳过认证
        if not api_key:
            return await call_next(request)

        path = request.url.path

        # 白名单路径放行
        if path in PUBLIC_PATHS:
            return await call_next(request)

        # 静态文件放行
        if path.startswith("/static/") or path.startswith("/generated_images/"):
            return await call_next(request)

        # 校验 API Key
        request_key = request.headers.get("X-API-Key", "")
        if request_key != api_key:
            return JSONResponse(
                status_code=401,
                content={"detail": "无效或缺失 API Key，请在请求头中设置 X-API-Key"},
            )

        return await call_next(request)
