"""
GraphRAG Web 界面
基于 FastAPI 的 Web 应用，使用 LangGraph + LangChain 提供带会话记忆的知识图谱问答。

app 装配与生命周期在本文件；服务实例与依赖注入见 web/deps.py；
路由按模块拆分在 web/routers/ 下。
"""

import os
from contextlib import asynccontextmanager
from datetime import datetime

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from src.exceptions import GraphRAGError, ConfigError, ConnectionError_
from src.logging_config import setup_logging
from src.settings import get_settings
from web import deps

# 初始化日志
settings = get_settings()
setup_logging(log_level=settings.log_level, log_format=settings.log_format)
logger = structlog.get_logger(__name__)

# 路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
static_folder = os.path.join(project_root, 'static')
generated_images_dir = os.path.join(project_root, 'data', 'generated_images')

os.makedirs(generated_images_dir, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import anyio.to_thread

    # def 路由走 anyio 默认线程池（默认 40），并发场景下适当放大
    anyio.to_thread.current_default_thread_limiter().total_tokens = 64

    s = get_settings()
    if not s.fast_start:
        try:
            deps.init_langgraph_engine()
        except Exception:
            logger.warning("LangGraph 引擎初始化失败，问答功能不可用")
        deps.init_image_generator()
        deps.init_mastery_tracker()
        deps.init_quiz_service()
        deps.init_question_generator()
        deps.init_exam_service()
        deps.init_dashboard_service()
        deps.init_trial_service()
    else:
        logger.info("快速启动模式：系统将在首次查询时初始化")
    yield
    deps.shutdown()


# FastAPI 实例
app = FastAPI(title="GraphRAG 知识图谱问答系统", version="2.0", lifespan=lifespan)

# ==================== 中间件 ====================

from web.middleware import APIKeyMiddleware, RequestIDMiddleware
from web.rate_limiter import RateLimitMiddleware

app.add_middleware(APIKeyMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestIDMiddleware)

app.mount("/static", StaticFiles(directory=static_folder), name="static")
app.mount("/generated_images", StaticFiles(directory=generated_images_dir), name="generated_images")


# ==================== 全局异常处理 ====================

@app.exception_handler(GraphRAGError)
async def graphrag_error_handler(request: Request, exc: GraphRAGError):
    status_map = {
        ConfigError: 500,
        ConnectionError_: 503,
    }
    status = status_map.get(type(exc), 500)
    logger.error(
        "GraphRAG 错误",
        error=exc.message,
        detail=exc.detail,
        error_type=type(exc).__name__,
    )
    return JSONResponse(
        status_code=status,
        content={
            "error": type(exc).__name__,
            "message": exc.message,
            "detail": exc.detail,
        },
    )


# ==================== 应用级自描述 API ====================

@app.get("/api/config")
async def get_config():
    return {
        "features": {
            "voice_input": True,
            "history": True,
            "export": True,
            "theme": True,
            "fullscreen": True,
            "session_memory": deps.langgraph_engine is not None,
            "image_generation": deps.image_generator is not None and deps.image_generator.is_available(),
        },
        "limits": {"max_query_length": 2000, "max_history_items": 20},
    }


@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "langgraph_engine_initialized": deps.langgraph_engine is not None,
    }


# ==================== 路由注册 ====================

from web.routers import dashboard, exam, graph, mastery, pages, query, quiz, trial

app.include_router(pages.router)
app.include_router(query.router)
app.include_router(graph.router)
app.include_router(mastery.router)
app.include_router(quiz.router)
app.include_router(exam.router)
app.include_router(dashboard.router)
app.include_router(trial.router)


# ==================== 启动入口 ====================

def main():
    import uvicorn
    s = get_settings()
    logger.info("启动 GraphRAG Web 应用", host=s.web_host, port=s.web_port, debug=s.debug)
    uvicorn.run(
        "web.graph_rag_web:app",
        host=s.web_host,
        port=s.web_port,
        reload=s.debug,
    )


if __name__ == '__main__':
    main()
