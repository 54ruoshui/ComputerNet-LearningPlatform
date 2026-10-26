"""双桶限流中间件单元测试 — 静态放行、会话桶、IP 天花板、body 重放"""

import json

import pytest
from fastapi import FastAPI, Request
from starlette.testclient import TestClient

from web.rate_limiter import _rate_limiter, RateLimitMiddleware


@pytest.fixture(autouse=True)
def _clear_buckets():
    _rate_limiter._buckets.clear()
    yield
    _rate_limiter._buckets.clear()


@pytest.fixture
def app():
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.get("/")
    async def index():
        return {"page": "index"}

    @app.get("/static/{path:path}")
    async def static_file(path: str):
        return {"path": path}

    @app.get("/api/query")
    async def query_get(session_id: str = None):
        return {"session_id": session_id}

    @app.post("/api/query")
    async def query(request: Request):
        # 容忍非 JSON body：同时验证重放的 body 完整到达路由
        raw = await request.body()
        try:
            body = json.loads(raw)
        except ValueError:
            return {"raw": raw.decode()}
        return {"session_id": body.get("session_id"), "question": body.get("question")}

    @app.post("/api/quiz/generate")
    async def quiz_generate(request: Request):
        raw = await request.body()
        try:
            body = json.loads(raw)
        except ValueError:
            return {"raw": raw.decode()}
        return {"ok": True, "session_id": body.get("session_id")}

    return app


class TestScopeExemptions:
    def test_static_and_pages_not_limited(self, app):
        client = TestClient(app)
        for _ in range(120):
            assert client.get("/static/app.js").status_code == 200
            assert client.get("/").status_code == 200


class TestSessionBucket:
    def test_query_session_limit_10_per_60s(self, app):
        """同一 session 第 11 次 query 429，且每次 body 都完整到达（重放正确）"""
        client = TestClient(app)
        for i in range(10):
            resp = client.post("/api/query", json={"session_id": "s1", "question": f"q{i}"})
            assert resp.status_code == 200
            assert resp.json()["question"] == f"q{i}"  # body 未被限流器吞掉

        blocked = client.post("/api/query", json={"session_id": "s1", "question": "again"})
        assert blocked.status_code == 429
        assert blocked.headers["Retry-After"] == "60"
        assert "请求过于频繁" in blocked.json()["detail"]

    def test_two_sessions_share_ip_each_pass(self, app):
        """共用 IP（班级 NAT）下两会话互不挤占：各 10 次全部通过"""
        client = TestClient(app)
        for i in range(10):
            assert client.post("/api/query", json={"session_id": "s1", "question": "q"}).status_code == 200
            assert client.post("/api/query", json={"session_id": "s2", "question": "q"}).status_code == 200

    def test_query_string_session_extracted(self, app):
        """GET 请求从 ?session_id= 提取（无需读 body）"""
        client = TestClient(app)
        for _ in range(10):
            assert client.get("/api/query", params={"session_id": "s1"}).status_code == 200
        assert client.get("/api/query", params={"session_id": "s1"}).status_code == 429

    def test_no_session_only_ip_bucket(self, app):
        """body 不含 session_id 的 POST 只走 IP 桶（query IP 桶 150/60s，20 次应全过）"""
        client = TestClient(app)
        for _ in range(20):
            resp = client.post("/api/query", json={"question": "q"})
            assert resp.status_code == 200

    def test_non_json_body_passes_through_intact(self, app):
        """非 JSON body 不做会话分桶，且原样到达路由"""
        client = TestClient(app)
        for _ in range(11):  # 若被错误计入会话桶，第 11 次将 429
            resp = client.post("/api/query", content=b"plain text payload",
                               headers={"Content-Type": "text/plain"})
            assert resp.status_code == 200
            assert resp.json()["raw"] == "plain text payload"

    def test_oversize_body_not_sniffed(self, app):
        """超过 64KB 的 body 不读取（不进会话桶），路由仍能收到完整 body"""
        client = TestClient(app)
        big = "x" * (70 * 1024)
        for _ in range(11):
            resp = client.post("/api/query", content=big.encode(),
                               headers={"Content-Type": "text/plain"})
            assert resp.status_code == 200
            assert resp.json()["raw"] == big


class TestIPCeiling:
    def test_generate_ip_limit_30(self, app):
        """/api/quiz/generate IP 桶 30/60s：第 31 次被天花板拦下"""
        client = TestClient(app)
        for i in range(30):
            resp = client.post("/api/quiz/generate", json={"prompt": f"p{i}"})
            assert resp.status_code == 200

        blocked = client.post("/api/quiz/generate", json={"prompt": "more"})
        assert blocked.status_code == 429

    def test_distinct_sessions_cannot_bypass_ip_ceiling(self, app):
        """换 session_id 也绕不开 IP 天花板（30 次后拦）"""
        client = TestClient(app)
        for i in range(30):
            resp = client.post("/api/quiz/generate", json={"session_id": f"s{i}", "prompt": "p"})
            assert resp.status_code == 200
        assert client.post("/api/quiz/generate", json={"session_id": "s-new", "prompt": "p"}).status_code == 429
