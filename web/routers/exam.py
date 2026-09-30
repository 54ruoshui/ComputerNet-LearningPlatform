"""考试试卷 API"""

from typing import Dict

import structlog
from fastapi import APIRouter, Depends, HTTPException

from web.deps import get_exam_service
from web.schemas import ExamSubmitRequest

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/exam", tags=["exam"])


@router.get("/papers")
def get_exam_papers(service=Depends(get_exam_service)):
    try:
        return service.get_exam_papers()
    except Exception as e:
        logger.error("获取试卷列表失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/papers/{paper_id}")
def get_exam_paper(paper_id: str, service=Depends(get_exam_service)):
    try:
        paper = service.get_exam_paper(paper_id)
        if not paper:
            raise HTTPException(status_code=404, detail="试卷不存在")
        return paper
    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取试卷详情失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/papers/import")
def import_exam_paper(data: Dict, service=Depends(get_exam_service)):
    try:
        paper_id = service.create_exam_paper(data)
        return {"success": True, "paper_id": paper_id}
    except Exception as e:
        logger.error("导入试卷失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/submit")
def submit_exam(req: ExamSubmitRequest, service=Depends(get_exam_service)):
    try:
        result = service.submit_exam(
            req.session_id, req.paper_id, req.answers
        )
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])

        # 附加 LLM 分析
        wrong_details = [d for d in result.get("details", []) if not d.get("correct")]
        analysis = service.analyze_weaknesses(
            session_id=req.session_id, wrong_details=wrong_details
        )
        result["analysis"] = analysis

        # 附加错误统计
        if req.session_id:
            result["error_stats"] = service.get_exam_error_stats(req.session_id)

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("提交试卷失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/analysis/{session_id}")
def get_exam_analysis(session_id: str, service=Depends(get_exam_service)):
    try:
        analysis = service.analyze_weaknesses(session_id)
        return analysis
    except Exception as e:
        logger.error("获取分析失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/recommendations/{session_id}")
def get_exam_recommendations(session_id: str, limit: int = 20, service=Depends(get_exam_service)):
    try:
        return service.get_recommendations(session_id, limit=limit)
    except Exception as e:
        logger.error("获取推荐失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history/{session_id}")
def get_exam_history(session_id: str, service=Depends(get_exam_service)):
    try:
        return service.get_exam_history(session_id)
    except Exception as e:
        logger.error("获取考试历史失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/result/{result_id}")
def get_exam_result(result_id: int, service=Depends(get_exam_service)):
    try:
        result = service.get_exam_result(result_id)
        if not result:
            raise HTTPException(status_code=404, detail="考试记录不存在")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取考试结果失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
