"""核心查询 + 会话记忆 API"""

import asyncio
from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from src.logging_config import session_id_var
from web import deps
from web.deps import get_langgraph_engine
from web.schemas import QueryRequest

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api", tags=["query"])


@router.post("/query")
def query(req: QueryRequest, engine=Depends(get_langgraph_engine)):
    try:
        if req.session_id:
            session_id_var.set(req.session_id)
        logger.info("LangGraph 查询", session_id=req.session_id, question=req.question[:100])

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
    except HTTPException:
        raise
    except Exception as e:
        logger.error("查询处理失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/query/stream")
async def query_stream(req: QueryRequest, engine=Depends(get_langgraph_engine)):
    """流式查询：SSE 逐 token 推送 + 工具状态 + 图谱数据"""
    if req.session_id:
        session_id_var.set(req.session_id)

    mastery_context = ""
    if req.session_id and deps.mastery_service:
        mastery_context = await asyncio.to_thread(
            deps.mastery_service.build_mastery_context, req.session_id
        )

    return StreamingResponse(
        engine.query_stream(
            req.question,
            session_id=req.session_id,
            mastery_context=mastery_context,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/sessions")
def list_sessions(engine=Depends(get_langgraph_engine)):
    try:
        sessions = engine.list_sessions()
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
def new_session(engine=Depends(get_langgraph_engine)):
    session_id = engine.new_session()
    return {"session_id": session_id}


@router.get("/sessions/{session_id}/history")
def session_history(session_id: str, engine=Depends(get_langgraph_engine)):
    history = engine.get_session_history(session_id)
    return {"session_id": session_id, "history": history}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, engine=Depends(get_langgraph_engine)):
    success = engine.delete_session(session_id)
    if success:
        return {"success": True}
    raise HTTPException(status_code=404, detail="会话不存在")
