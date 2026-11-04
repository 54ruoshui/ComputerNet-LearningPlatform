"""掌握状态 + 复习调度 + 答案满意度反馈 API（同属 MasteryService 门面）"""

from typing import Optional

import structlog
from fastapi import APIRouter, HTTPException

from web import deps
from web.schemas import (
    FeedbackRequest,
    MasteryBatchRequest,
    MasteryRequest,
    ReviewRecordRequest,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api", tags=["mastery"])


def _require_mastery(detail: str):
    service = deps.mastery_service
    if not service or not service.available:
        raise HTTPException(status_code=503, detail=detail)
    return service


@router.get("/mastery/{session_id}")
def get_mastery(session_id: str):
    service = _require_mastery("掌握追踪器未初始化")
    return service.get_mastery(session_id)


@router.post("/mastery")
def set_mastery(req: MasteryRequest):
    service = _require_mastery("掌握追踪器未初始化")
    service.set_mastery(req.session_id, req.entity_name, req.mastered)
    return {"success": True}


@router.post("/mastery/batch")
def set_mastery_batch(req: MasteryBatchRequest):
    service = _require_mastery("掌握追踪器未初始化")
    service.set_mastery_batch(req.session_id, req.entities)
    return {"success": True}


@router.get("/review/state/{session_id}")
def get_review_state(session_id: str):
    service = _require_mastery("复习调度器未初始化")
    try:
        return service.get_review_state(session_id)
    except Exception as e:
        logger.error("获取复习状态失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/review/due/{session_id}")
def get_due_reviews(session_id: str, lookahead: float = 48, limit: int = 50):
    service = _require_mastery("复习调度器未初始化")
    try:
        return service.get_due_reviews(session_id, lookahead, limit)
    except Exception as e:
        logger.error("获取到期复习失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/review/record")
def record_review(req: ReviewRecordRequest):
    service = _require_mastery("复习调度器未初始化")
    try:
        result = service.record_review(
            req.session_id, req.entity_name, req.quality, req.event_type
        )
        return result
    except Exception as e:
        logger.error("记录复习失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/review/stats/{session_id}")
def get_review_stats(session_id: str):
    service = _require_mastery("复习调度器未初始化")
    try:
        return service.get_review_stats(session_id)
    except Exception as e:
        logger.error("获取复习统计失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/feedback")
def submit_feedback(req: FeedbackRequest):
    service = _require_mastery("反馈服务未初始化")
    try:
        row_id = service.record_feedback(
            req.session_id or "", req.rating, req.question, req.comment
        )
        return {"success": True, "id": row_id}
    except Exception as e:
        logger.error("保存反馈失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/feedback/stats")
def get_feedback_stats(session_id: Optional[str] = None):
    service = _require_mastery("反馈服务未初始化")
    try:
        return service.get_feedback_stats(session_id)
    except Exception as e:
        logger.error("获取反馈统计失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
