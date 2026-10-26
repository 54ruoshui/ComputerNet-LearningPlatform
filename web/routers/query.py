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

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api", tags=["query"])

GATE_BUSY_MESSAGE = "当前查询人数较多，请稍后重试"


def _visitor(request: Request) -> str | None:
    vid = request.scope.get("aigc_visitor")
    if vid:
        return vid
    return request.cookies.get("aigc_visitor")


def _bind_session(session_id: str, request: Request):
    """把会话绑定到当前访客（best-effort；tracker 不可用时静默跳过）"""
    tracker = deps._get_tracker()
    visitor = _visitor(request)
    if tracker and visitor:
        try:
            tracker.bind_session(session_id, visitor)
        except Exception as e:
            logger.warning("会话归属绑定失败", session_id=session_id, error=str(e))


def _ensure_session_owner(session_id: str, request: Request) -> bool:
    """查询路径的归属校验：未绑定会话就地懒绑定（治愈存量会话），绑定他人返回 False。

    tracker/访客不可用时放行（fail-open，与其余 mastery 降级行为一致）。
    """
    tracker = deps._get_tracker()
    visitor = _visitor(request)
    if not tracker or not visitor:
        return True
    owner = tracker.get_session_owner(session_id)
    if owner is None:
        _bind_session(session_id, request)
        return True
    return owner == visitor


def _session_accessible(session_id: str, request: Request) -> bool:
    """history/delete 的归属校验：未绑定或绑定他人一律视为不存在（fail-open 同上）"""
    tracker = deps._get_tracker()
    visitor = _visitor(request)
    if not tracker or not visitor:
        return True
    owner = tracker.get_session_owner(session_id)
    return owner is not None and owner == visitor


@router.post("/query")
def query(req: QueryRequest, request: Request, engine=Depends(get_langgraph_engine)):
    try:
        if req.session_id:
            session_id_var.set(req.session_id)
            if not _ensure_session_owner(req.session_id, request):
                raise HTTPException(status_code=404, detail="会话不存在")

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
    if req.session_id:
        session_id_var.set(req.session_id)
        if not _ensure_session_owner(req.session_id, request):
            raise HTTPException(status_code=404, detail="会话不存在")

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
        sessions = engine.list_sessions()
        tracker = deps._get_tracker()
        visitor = _visitor(request)
        if tracker and visitor:
            owned = set(tracker.list_sessions_for_visitor(visitor))
            sessions = [s for s in sessions if s in owned]
        summaries = []
        for sid in sessions:
            history = engine.get_session_history(sid)
            if history:
                summaries.append({
                    "session_id": sid,
                    "last_question": next(
                        (m["content"] for m in reversed(history) if m["role"] == "user"), ""
                    ),
                    "message_count": len(history),
                })
        return {"sessions": summaries}
    except Exception as e:
        logger.error("列出会话失败", error=str(e))
        return {"sessions": []}


@router.post("/sessions/new")
def new_session(request: Request, engine=Depends(get_langgraph_engine)):
    session_id = engine.new_session()
    _bind_session(session_id, request)
    return {"session_id": session_id}


@router.get("/sessions/{session_id}/history")
def session_history(session_id: str, request: Request, engine=Depends(get_langgraph_engine)):
    if not _session_accessible(session_id, request):
        raise HTTPException(status_code=404, detail="会话不存在")
    history = engine.get_session_history(session_id)
    return {"session_id": session_id, "history": history}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, request: Request, engine=Depends(get_langgraph_engine)):
    if not _session_accessible(session_id, request):
        raise HTTPException(status_code=404, detail="会话不存在")
    success = engine.delete_session(session_id)
    if success:
        tracker = deps._get_tracker()
        if tracker:
            try:
                tracker.unbind_session(session_id)
            except Exception:
                pass
        return {"success": True}
    raise HTTPException(status_code=404, detail="会话不存在")
