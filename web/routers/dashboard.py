"""学习仪表盘 API"""

import structlog
from fastapi import APIRouter, Depends, HTTPException

from web.deps import get_dashboard_service

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/overview/{session_id}")
def get_dashboard_overview(session_id: str, service=Depends(get_dashboard_service)):
    try:
        return service.get_overview(session_id)
    except Exception as e:
        logger.error("获取仪表盘数据失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/report/{session_id}")
def generate_dashboard_report(session_id: str, service=Depends(get_dashboard_service)):
    try:
        report = service.generate_report(session_id)
        return {"report": report}
    except Exception as e:
        logger.error("生成学习报告失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
