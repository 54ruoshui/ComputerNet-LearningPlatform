"""LLM 并发闸门 — 限制同时进行的 LLM 长任务数（查询/分析/报告）。

单进程部署下 LLM 上游吞吐有限，放任并发只会堆高延迟与超时；
超出闸门容量的请求排队等待，等不到则快速失败（503/降级）。
"""

import asyncio
import threading

import structlog

from src.settings import get_settings

logger = structlog.get_logger(__name__)

_ACQUIRE_POLL_INTERVAL = 0.2  # 异步等待的轮询间隔（秒）


class LLMConcurrencyGate:
    def __init__(self, max_concurrency: int, queue_timeout: float):
        self._semaphore = threading.BoundedSemaphore(max_concurrency)
        self._queue_timeout = queue_timeout

    def acquire(self, timeout: float | None = None) -> bool:
        """同步获取（def 路由在线程池里阻塞等待），超时返回 False。"""
        effective = self._queue_timeout if timeout is None else timeout
        if effective <= 0:
            return self._semaphore.acquire(blocking=False)
        return self._semaphore.acquire(timeout=effective)

    async def acquire_async(self, timeout: float | None = None) -> bool:
        """异步获取：非阻塞轮询，不占用线程池线程。"""
        effective = self._queue_timeout if timeout is None else timeout
        loop = asyncio.get_running_loop()
        deadline = loop.time() + effective
        while True:
            if self._semaphore.acquire(blocking=False):
                return True
            if effective <= 0 or loop.time() >= deadline:
                return False
            await asyncio.sleep(_ACQUIRE_POLL_INTERVAL)

    def release(self):
        """释放；超量释放说明获取/释放不配对，记日志不抛出（避免拖垮请求）。"""
        try:
            self._semaphore.release()
        except ValueError:
            logger.error("并发闸门超量释放（acquire/release 不配对）")


_gate: LLMConcurrencyGate | None = None
_gate_lock = threading.Lock()


def get_gate() -> LLMConcurrencyGate:
    global _gate
    if _gate is None:
        with _gate_lock:
            if _gate is None:
                s = get_settings()
                _gate = LLMConcurrencyGate(s.agent_max_concurrency, s.agent_queue_timeout)
    return _gate


def reset_gate():
    """测试钩子：强制下次 get_gate() 读取最新配置重建闸门。"""
    global _gate
    with _gate_lock:
        _gate = None
