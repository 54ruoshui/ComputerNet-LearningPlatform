"""
结构化日志配置
基于 structlog，支持 console（开发）和 JSON（生产）两种输出格式。
"""

import logging
import uuid
from contextvars import ContextVar

import structlog

request_id_var: ContextVar[str] = ContextVar("request_id", default="")
session_id_var: ContextVar[str] = ContextVar("session_id", default="")


def _add_context_vars(logger, method_name, event_dict):
    """自动注入 request_id 和 session_id"""
    rid = request_id_var.get("")
    if rid:
        event_dict["request_id"] = rid
    sid = session_id_var.get("")
    if sid:
        event_dict["session_id"] = sid
    return event_dict


def setup_logging(log_level: str = "INFO", log_format: str = "console"):
    """
    初始化 structlog。

    Args:
        log_level: 日志级别
        log_format: "console"（可读）或 "json"（机器可解析）
    """
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        _add_context_vars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if log_format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))


def new_request_id() -> str:
    """生成新的 request_id 并设置到上下文"""
    rid = uuid.uuid4().hex[:12]
    request_id_var.set(rid)
    return rid
