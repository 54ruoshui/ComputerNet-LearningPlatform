"""教师端班级学情 API（teacher/admin 角色可用，全部只读）"""

import structlog
from fastapi import APIRouter, Depends, HTTPException

from web.concurrency import get_gate
from web.deps import _get_tracker, get_dashboard_service, require_teacher

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/teacher", tags=["teacher"], dependencies=[Depends(require_teacher)])


@router.get("/students")
def list_students():
    tracker = _get_tracker()
    if tracker is None:
        raise HTTPException(status_code=503, detail="学习数据服务不可用")
    try:
        return {"students": tracker.list_student_summaries()}
    except Exception as e:
        logger.error("获取班级学情失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/students/{session_id}/overview")
def student_overview(session_id: str, service=Depends(get_dashboard_service)):
    try:
        return service.get_overview(session_id)
    except Exception as e:
        logger.error("获取学生学情概览失败", error=str(e), session_id=session_id)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/students/{session_id}/report")
def student_report(session_id: str, service=Depends(get_dashboard_service)):
    gate = get_gate()
    if not gate.acquire():
        raise HTTPException(status_code=503, detail="当前请求人数较多，请稍后重试")
    try:
        return {"report": service.generate_report(session_id)}
    except Exception as e:
        logger.error("生成学生学习报告失败", error=str(e), session_id=session_id)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        gate.release()
