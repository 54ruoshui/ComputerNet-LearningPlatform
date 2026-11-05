"""核心查询 + 会话记忆 API（会话归属按访客 cookie 隔离）"""

import asyncio
import json
from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.logging_config import session_id_var
from web import deps
from web.concurrency import get_gate
from web.deps import get_langgraph_engine
from web.schemas import QueryRequest
from web.session_access import (
    bind_session,
    ensure_session_owner,
    session_accessible,
    visitor_id,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api", tags=["query"])

GATE_BUSY_MESSAGE = "当前查询人数较多，请稍后重试"


def _prepare_session(req: QueryRequest, request: Request, engine) -> None:
    """会话预处理：已有会话校验归属；未带 session_id 就地新建并绑定访客。

    隐式创建的会话若不绑定访客，将不会出现在该访客的会话列表里。
    """
    if req.session_id:
        session_id_var.set(req.session_id)
        if not ensure_session_owner(req.session_id, request):
            raise HTTPException(status_code=404, detail="会话不存在")
    else:
        req.session_id = engine.new_session()
        session_id_var.set(req.session_id)
        bind_session(req.session_id, request)


@router.post("/query")
def query(req: QueryRequest, request: Request, engine=Depends(get_langgraph_engine)):
    try:
        _prepare_session(req, request, engine)

        logger.info("LangGraph 查询", session_id=req.session_id, question=req.question[:100])

        gate = get_gate()
        if not gate.acquire():
            raise HTTPException(status_code=503, detail=GATE_BUSY_MESSAGE)
        try:
            mastery_context = ""
            if req.session_id and deps.mastery_service:
                mastery_context = deps.mastery_service.build_mastery_context(req.session_id)

            response = engine.query(
                req.question,
                session_id=req.session_id,
                mastery_context=mastery_context,
            )

            result = {
                "question": response.get("question", req.question),
                "answer": response.get("answer", "未获取到答案"),
                "processing_time": response.get("processing_time", 0),
                "timestamp": datetime.now().isoformat(),
                "session_id": response.get("session_id"),
            }

            try:
                entity_names = deps.entity_service.get_all_entity_names() if deps.entity_service else []
                extracted = deps.entity_service.extract_entities_from_text(
                    req.question + " " + result["answer"], entity_names
                ) if deps.entity_service else []
                result["extracted_entities"] = extracted[:10]
            except Exception:
                result["extracted_entities"] = []
            if response.get("context_length"):
                result["context_length"] = response["context_length"]
            if response.get("graph_data"):
                result["graph_data"] = response["graph_data"]

            return JSONResponse(content=result)
        finally:
            gate.release()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("查询处理失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/query/stream")
async def query_stream(req: QueryRequest, request: Request, engine=Depends(get_langgraph_engine)):
    """流式查询：SSE 逐 token 推送 + 工具状态 + 图谱数据"""
    _prepare_session(req, request, engine)

    mastery_context = ""
    if req.session_id and deps.mastery_service:
        mastery_context = await asyncio.to_thread(
            deps.mastery_service.build_mastery_context, req.session_id
        )

    gate = get_gate()
    if not await gate.acquire_async():
        async def _busy_stream():
            yield f'event: error\ndata: {json.dumps({"msg": GATE_BUSY_MESSAGE}, ensure_ascii=False)}\n\n'

        return StreamingResponse(
            _busy_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    async def _gated():
        try:
            async for chunk in engine.query_stream(
                req.question,
                session_id=req.session_id,
                mastery_context=mastery_context,
            ):
                yield chunk
        finally:
            gate.release()  # 客户端断开（生成器被关闭）也会走到这里

    return StreamingResponse(
        _gated(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/sessions")
def list_sessions(request: Request, engine=Depends(get_langgraph_engine)):
    try:
        threads = engine.list_session_summaries()
        tracker = deps._get_tracker()
        visitor = visitor_id(request)
        user = deps.resolve_user(request)
        user_set = set()
        if user and deps.auth_service:
            user_set = {s["session_id"] for s in deps.auth_service.list_user_sessions(user["id"])}
        owned = None
        if tracker and visitor:
            owned = set(tracker.list_sessions_for_visitor(visitor)) | user_set
        summaries = []
        for thread in threads:
            sid = thread["session_id"]
            if owned is not None and sid not in owned:
                continue
            history = engine.get_session_history(sid)
            if not history:
                continue
            user_rounds = [m["content"] for m in history if m["role"] == "user"]
            summaries.append({
                "session_id": sid,
                "title": (user_rounds[0].strip()[:60] if user_rounds else "新对话"),
                "rounds": len(user_rounds),
                "updated_at": thread.get("updated_at", ""),
                "user_owned": sid in user_set,
            })
            if len(summaries) >= 50:
                break
        return {"sessions": summaries}
    except Exception as e:
        logger.error("列出会话失败", error=str(e))
        return {"sessions": []}


@router.post("/sessions/new")
def new_session(request: Request, engine=Depends(get_langgraph_engine)):
    session_id = engine.new_session()
    bind_session(session_id, request)
    # 已登录用户的新会话同时记入账号（best-effort，游客路径不受影响）
    user = deps.resolve_user(request)
    if user and deps.auth_service:
        try:
            deps.auth_service.attach_session(user["id"], session_id)
        except Exception as e:
            logger.warning("会话记入账号失败", session_id=session_id, error=str(e))
    return {"session_id": session_id}


@router.get("/sessions/{session_id}/history")
def session_history(session_id: str, request: Request, engine=Depends(get_langgraph_engine)):
    if not session_accessible(session_id, request):
        raise HTTPException(status_code=404, detail="会话不存在")
    history = engine.get_session_history(session_id)
    return {"session_id": session_id, "history": history}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, request: Request, engine=Depends(get_langgraph_engine)):
    if not session_accessible(session_id, request):
        raise HTTPException(status_code=404, detail="会话不存在")
    success = engine.delete_session(session_id)
    if success:
        tracker = deps._get_tracker()
        if tracker:
            try:
                tracker.unbind_session(session_id)
            except Exception:
                pass
        if deps.auth_service:
            try:
                deps.auth_service.detach_session(session_id)
            except Exception:
                pass
        return {"success": True}
    raise HTTPException(status_code=404, detail="会话不存在")
