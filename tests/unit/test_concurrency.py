"""LLM 并发闸门单元测试 — 同步/异步获取、超时、释放安全、串行化"""

import asyncio
import threading
import time

from web.concurrency import LLMConcurrencyGate, get_gate, reset_gate


class TestSyncAcquire:

    def test_acquire_within_capacity(self):
        gate = LLMConcurrencyGate(2, 1.0)
        assert gate.acquire()
        assert gate.acquire()

    def test_acquire_timeout_returns_false(self):
        gate = LLMConcurrencyGate(1, 0.05)
        assert gate.acquire()
        assert gate.acquire() is False  # 排队 50ms 后放弃

    def test_zero_timeout_immediate_reject(self):
        gate = LLMConcurrencyGate(1, 0)
        assert gate.acquire()
        assert gate.acquire() is False

    def test_release_enables_next(self):
        gate = LLMConcurrencyGate(1, 0.05)
        assert gate.acquire()
        gate.release()
        assert gate.acquire()


class TestAsyncAcquire:

    def test_acquire_within_capacity(self):
        gate = LLMConcurrencyGate(1, 1.0)
        assert asyncio.run(gate.acquire_async())
        gate.release()

    def test_acquire_timeout_returns_false(self):
        gate = LLMConcurrencyGate(1, 0.05)

        async def scenario():
            assert await gate.acquire_async()
            started = time.monotonic()
            got = await gate.acquire_async()
            assert got is False
            assert time.monotonic() - started >= 0.04

        asyncio.run(scenario())

    def test_acquire_waits_until_release(self):
        gate = LLMConcurrencyGate(1, 5.0)

        async def scenario():
            assert await gate.acquire_async()
            threading.Timer(0.1, gate.release).start()
            started = time.monotonic()
            assert await gate.acquire_async()  # 等到释放而非超时
            assert time.monotonic() - started < 2

        asyncio.run(scenario())


class TestReleaseSafety:

    def test_double_release_does_not_raise(self):
        gate = LLMConcurrencyGate(1, 0)
        gate.acquire()
        gate.release()
        gate.release()  # 超量释放仅记日志


class TestSerialization:

    def test_max_1_serializes_workers(self):
        gate = LLMConcurrencyGate(1, 5.0)
        active = 0
        peak = 0
        lock = threading.Lock()

        def worker():
            nonlocal active, peak
            assert gate.acquire()
            try:
                with lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.02)
                with lock:
                    active -= 1
            finally:
                gate.release()

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert peak == 1


class TestSingleton:

    def test_gate_cached_and_reset(self):
        reset_gate()
        g1 = get_gate()
        assert get_gate() is g1
        reset_gate()
        assert get_gate() is not g1
