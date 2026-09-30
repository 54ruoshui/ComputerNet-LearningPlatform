"""刷题测验 + AI 习题生成 + 错题/已做记录 API"""

from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException

from src.exceptions import GraphRAGError
from src.services import quiz_storage
from web import deps
from web.deps import get_question_generator, get_quiz_service
from web.schemas import (
    MCQuestionCreate,
    QuizAnswerRequest,
    QuizGenerateRequest,
    WrongEntry,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/quiz", tags=["quiz"])


@router.get("/questions")
def get_quiz_questions(
    layer: Optional[str] = None,
    difficulty: Optional[str] = None,
    chapter: Optional[str] = None,
    limit: int = 20,
    service=Depends(get_quiz_service),
):
    try:
        return service.get_questions(
            layer=layer, difficulty=difficulty, chapter=chapter, limit=limit
        )
    except Exception as e:
        logger.error("获取测验题目失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chapters")
def get_chapter_structure(service=Depends(get_quiz_service)):
    try:
        return service.get_chapter_structure()
    except Exception as e:
        logger.error("获取章节结构失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/answer")
def submit_quiz_answer(req: QuizAnswerRequest, service=Depends(get_quiz_service)):
    try:
        result = service.check_answer(req.question_id)
        if not result:
            raise HTTPException(status_code=404, detail="题目不存在")

        is_correct = req.selected_answer == result["correct_answer"]

        # 联动复习调度：答题结果驱动遗忘曲线更新，掌握度由曲线状态自动判定
        if req.session_id and deps.mastery_service and deps.mastery_service.available:
            refs = result.get("references", [])
            quality = 4 if is_correct else 1
            event_type = "quiz_correct" if is_correct else "quiz_wrong"
            chapter = result.get("chapter", "")
            for entity_name in refs:
                try:
                    deps.mastery_service.record_review(
                        req.session_id, entity_name, quality, event_type, chapter
                    )
                except Exception:
                    pass  # 复习记录失败不影响答题流程

        return {
            "correct": is_correct,
            "correct_answer": result["correct_answer"],
            "explanation": result["explanation"],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("提交答案失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
def get_quiz_stats(service=Depends(get_quiz_service)):
    try:
        return service.get_stats()
    except Exception as e:
        logger.error("获取测验统计失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/questions")
def create_quiz_question(req: MCQuestionCreate, service=Depends(get_quiz_service)):
    try:
        q_id = service.add_question(req.model_dump())
        return {"success": True, "question_id": q_id}
    except Exception as e:
        logger.error("创建题目失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate")
def generate_quiz_questions(req: QuizGenerateRequest, generator=Depends(get_question_generator)):
    try:
        result = generator.generate(
            layer=req.layer,
            chapter=req.chapter,
            difficulty=req.difficulty,
            count=req.count,
            seed_entities=req.seed_entities,
        )
        return result
    except GraphRAGError as e:
        logger.error("生成题目失败", error=e.message, detail=e.detail)
        raise HTTPException(status_code=500, detail=e.detail or e.message)
    except Exception as e:
        logger.error("生成题目失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 错题集 & 已做题目 本地存储 ====================

@router.get("/done")
def api_get_done_ids():
    return {"ids": quiz_storage.get_done_ids()}


@router.post("/done/{question_id}")
def api_mark_done(question_id: str):
    return {"ids": quiz_storage.mark_done(question_id)}


@router.get("/wrong")
def api_get_wrong_list():
    return {"list": quiz_storage.get_wrong_list()}


@router.post("/wrong")
def api_add_wrong(entry: WrongEntry):
    return {"list": quiz_storage.add_wrong(entry.model_dump())}


@router.delete("/wrong/{question_id}")
def api_remove_wrong(question_id: str):
    return {"list": quiz_storage.remove_wrong(question_id)}


@router.delete("/wrong")
def api_clear_wrong():
    quiz_storage.clear_wrong()
    return {"success": True}
