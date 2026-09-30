"""
GraphRAG Web 界面
基于 FastAPI 的 Web 应用，使用 LangGraph + LangChain 提供带会话记忆的知识图谱问答。
"""

import asyncio
import os
from datetime import datetime
from typing import Dict, List, Optional

import structlog
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from src.exceptions import GraphRAGError, ConfigError, ConnectionError_
from src.logging_config import setup_logging, session_id_var
from src.services import quiz_storage
from src.settings import get_settings

# 初始化日志
settings = get_settings()
setup_logging(log_level=settings.log_level, log_format=settings.log_format)
logger = structlog.get_logger(__name__)

# 路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
template_folder = os.path.join(project_root, 'templates')
static_folder = os.path.join(project_root, 'static')
generated_images_dir = os.path.join(project_root, 'data', 'generated_images')

# FastAPI 实例
app = FastAPI(title="GraphRAG 知识图谱问答系统", version="2.0")

# ==================== 中间件 ====================

from web.middleware import APIKeyMiddleware, RequestIDMiddleware
from web.rate_limiter import RateLimitMiddleware

app.add_middleware(APIKeyMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestIDMiddleware)


app.mount("/static", StaticFiles(directory=static_folder), name="static")
templates = Jinja2Templates(directory=template_folder)

os.makedirs(generated_images_dir, exist_ok=True)
app.mount("/generated_images", StaticFiles(directory=generated_images_dir), name="generated_images")

# ==================== 全局实例 ====================
langgraph_engine = None
image_generator = None
entity_service = None
mastery_service = None
quiz_service = None
exam_service = None
dashboard_service = None
question_generator = None
trial_service = None


# ==================== Pydantic 请求模型（带校验） ====================

class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000, description="用户问题")
    session_id: Optional[str] = None


class ExportRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500, description="导出查询")


class GenerateImageRequest(BaseModel):
    graph_data: dict
    question: str = Field(..., min_length=1, max_length=500)


class MasteryRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=200)
    entity_name: str = Field(..., min_length=1, max_length=200)
    mastered: bool


class MasteryBatchRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=200)
    entities: List[Dict]


class QuizAnswerRequest(BaseModel):
    question_id: str = Field(..., min_length=1, max_length=100)
    selected_answer: str = Field(..., pattern="^[ABCD]$")
    session_id: Optional[str] = None


class FeedbackRequest(BaseModel):
    session_id: Optional[str] = Field(default=None, max_length=200)
    question: str = Field(default="", max_length=2000, description="被评价的提问文本")
    rating: int = Field(..., ge=0, le=1, description="1=满意 / 0=不满意")
    comment: str = Field(default="", max_length=500)


class QuizGenerateRequest(BaseModel):
    layer: Optional[str] = Field(default=None, pattern="^(物理层|数据链路层|网络层|传输层|应用层)$")
    chapter: Optional[str] = Field(default=None, max_length=10)
    difficulty: Optional[str] = Field(default=None, pattern="^(basic|medium|hard)$")
    count: int = Field(default=5, ge=1, le=10)
    seed_entities: Optional[List[str]] = Field(default=None, max_length=10)



class ExamSubmitRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=200)
    paper_id: str = Field(..., min_length=1, max_length=100)
    answers: Dict[str, str] = Field(..., description="题目ID→答案的映射，如 {'q1': 'A', 'q2': 'B'}")


class ReviewRecordRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=200)
    entity_name: str = Field(..., min_length=1, max_length=200)
    quality: int = Field(..., ge=0, le=5, description="回忆质量 0-5")
    event_type: str = Field(default="manual", pattern="^(quiz_correct|quiz_wrong|manual)$")


class MCQuestionCreate(BaseModel):
    text: str = Field(..., min_length=2, max_length=1000)
    option_a: str = Field(..., min_length=1, max_length=500)
    option_b: str = Field(..., min_length=1, max_length=500)
    option_c: str = Field(..., min_length=1, max_length=500)
    option_d: str = Field(..., min_length=1, max_length=500)
    correct_answer: str = Field(..., pattern="^[ABCD]$")
    explanation: str = Field(..., min_length=1, max_length=2000)
    difficulty: str = Field(..., pattern="^(basic|medium|hard)$")
    layer: str = Field(..., pattern="^(物理层|数据链路层|网络层|传输层|应用层)$")
    references: Optional[List[str]] = []
    source: str = Field(default="manual", pattern="^(manual|ai)$",
                        description="题目来源：手动添加或 AI 生成")


class TrialCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="试用批次名称")
    per_layer: int = Field(default=4, ge=2, le=10, description="每层抽取题量（前测/后测各 per_layer 题）")
    seed: Optional[int] = Field(default=None, ge=0, le=999999, description="随机种子，固定则平行卷可复现")


class TrialParticipantRequest(BaseModel):
    code: Optional[str] = Field(default=None, min_length=2, max_length=20, description="被试码，缺省自动生成")


class TrialJoinRequest(BaseModel):
    code: str = Field(..., min_length=2, max_length=20, description="被试码")
    session_id: str = Field(..., min_length=1, max_length=200, description="学习会话 ID")


class TrialTestStartRequest(BaseModel):
    code: str = Field(..., min_length=2, max_length=20, description="被试码")
    phase: str = Field(..., pattern="^(pre|post)$", description="pre=前测 / post=后测")


class TrialTestSubmitRequest(BaseModel):
    test_id: int = Field(..., ge=1)
    answers: Dict[str, str] = Field(..., description="题目ID→答案映射，如 {'q1': 'A'}")


# ==================== 全局异常处理 ====================

@app.exception_handler(GraphRAGError)
async def graphrag_error_handler(request: Request, exc: GraphRAGError):
    status_map = {
        ConfigError: 500,
        ConnectionError_: 503,
    }
    status = status_map.get(type(exc), 500)
    logger.error(
        "GraphRAG 错误",
        error=exc.message,
        detail=exc.detail,
        error_type=type(exc).__name__,
    )
    return JSONResponse(
        status_code=status,
        content={
            "error": type(exc).__name__,
            "message": exc.message,
            "detail": exc.detail,
        },
    )


# ==================== 初始化函数 ====================

def init_langgraph_engine():
    global langgraph_engine, entity_service
    try:
        logger.info("初始化 GraphRAG ReAct Agent...")
        from src.langchain_config import LangGraphRAGConfig
        from src.graphrag_agent import GraphRAGAgent
        from src.services.entity_service import EntityService
        config = LangGraphRAGConfig()
        langgraph_engine = GraphRAGAgent(config)
        entity_service = EntityService(langgraph_engine.driver)
        logger.info("GraphRAG ReAct Agent 初始化完成")
        return langgraph_engine
    except GraphRAGError:
        raise
    except Exception as e:
        logger.error("GraphRAG ReAct Agent 初始化失败", error=str(e))
        langgraph_engine = None
        return None


def init_image_generator():
    global image_generator
    try:
        logger.info("初始化 Graphviz 图片生成器...")
        from src.image_generator import KnowledgeGraphImageGenerator
        image_generator = KnowledgeGraphImageGenerator()
        if image_generator.is_available():
            logger.info("Graphviz 图片生成器初始化完成")
        else:
            logger.warning("Graphviz 未安装或不可用，图片生成功能已禁用")
        return image_generator
    except Exception as e:
        logger.error("Graphviz 图片生成器初始化失败", error=str(e))
        image_generator = None
        return None


def init_mastery_tracker():
    global mastery_service
    try:
        logger.info("初始化掌握追踪器...")
        from src.mastery_tracker import MasteryTracker
        from src.services.mastery_service import MasteryService
        db_path = os.path.join(project_root, "data", "memory", "mastery.db")
        tracker = MasteryTracker(db_path)
        mastery_service = MasteryService(tracker)
        logger.info("掌握追踪器初始化完成")
        return tracker
    except Exception as e:
        logger.error("掌握追踪器初始化失败", error=str(e))
        mastery_service = MasteryService(None)
        return None


def init_quiz_service():
    global quiz_service
    try:
        from src.services.quiz_service import QuizService
        driver = None
        if langgraph_engine and hasattr(langgraph_engine, "driver"):
            driver = langgraph_engine.driver
        else:
            from neo4j import GraphDatabase
            s = get_settings()
            driver = GraphDatabase.driver(
                s.neo4j_uri,
                auth=(s.neo4j_user, s.neo4j_password),
            )
        quiz_service = QuizService(driver)
        quiz_service.create_indexes()
        logger.info("QuizService 初始化完成")
    except Exception as e:
        logger.error("QuizService 初始化失败", error=str(e))
        quiz_service = None


def init_question_generator():
    global question_generator
    try:
        from src.services.question_generator import QuestionGenerator
        driver = None
        if langgraph_engine and hasattr(langgraph_engine, "driver"):
            driver = langgraph_engine.driver
        else:
            from neo4j import GraphDatabase
            s = get_settings()
            driver = GraphDatabase.driver(
                s.neo4j_uri,
                auth=(s.neo4j_user, s.neo4j_password),
            )
        question_generator = QuestionGenerator(driver)
        logger.info("QuestionGenerator 初始化完成")
    except Exception as e:
        logger.error("QuestionGenerator 初始化失败", error=str(e))
        question_generator = None


def init_exam_service():
    global exam_service
    try:
        from src.services.exam_service import ExamService
        driver = None
        if langgraph_engine and hasattr(langgraph_engine, "driver"):
            driver = langgraph_engine.driver
        else:
            from neo4j import GraphDatabase
            s = get_settings()
            driver = GraphDatabase.driver(
                s.neo4j_uri,
                auth=(s.neo4j_user, s.neo4j_password),
            )
        tracker = None
        if mastery_service and mastery_service.available:
            tracker = mastery_service._tracker
        exam_service = ExamService(driver, tracker)
        logger.info("ExamService 初始化完成")
    except Exception as e:
        logger.error("ExamService 初始化失败", error=str(e))
        exam_service = None


def init_dashboard_service():
    global dashboard_service
    try:
        from src.services.dashboard_service import DashboardService
        driver = None
        if langgraph_engine and hasattr(langgraph_engine, "driver"):
            driver = langgraph_engine.driver
        else:
            from neo4j import GraphDatabase
            s = get_settings()
            driver = GraphDatabase.driver(
                s.neo4j_uri,
                auth=(s.neo4j_user, s.neo4j_password),
            )
        tracker = None
        if mastery_service and mastery_service.available:
            tracker = mastery_service._tracker
        dashboard_service = DashboardService(driver, tracker, quiz_service)
        logger.info("DashboardService 初始化完成")
    except Exception as e:
        logger.error("DashboardService 初始化失败", error=str(e))
        dashboard_service = None


def init_trial_service():
    global trial_service
    try:
        from src.services.trial_service import TrialService
        driver = None
        if langgraph_engine and hasattr(langgraph_engine, "driver"):
            driver = langgraph_engine.driver
        else:
            from neo4j import GraphDatabase
            s = get_settings()
            driver = GraphDatabase.driver(
                s.neo4j_uri,
                auth=(s.neo4j_user, s.neo4j_password),
            )
        tracker = None
        if mastery_service and mastery_service.available:
            tracker = mastery_service._tracker
        if tracker is None:
            # TrialService 的全部功能依赖 SQLite 存储，无 tracker 视为初始化失败
            raise RuntimeError("MasteryTracker 不可用，TrialService 无法初始化")
        trial_service = TrialService(driver, tracker)
        logger.info("TrialService 初始化完成")
    except Exception as e:
        logger.error("TrialService 初始化失败", error=str(e))
        trial_service = None


def _require_engine():
    if not langgraph_engine:
        raise HTTPException(status_code=503, detail="LangGraph 引擎未初始化，请检查 Neo4j 和 API 配置")
    return langgraph_engine


# ==================== 启动/关闭事件 ====================

@app.on_event("startup")
def on_startup():
    s = get_settings()
    if not s.fast_start:
        try:
            init_langgraph_engine()
        except Exception:
            logger.warning("LangGraph 引擎初始化失败，问答功能不可用")
        init_image_generator()
        init_mastery_tracker()
        init_quiz_service()
        init_question_generator()
        init_exam_service()
        init_dashboard_service()
        init_trial_service()
    else:
        logger.info("快速启动模式：系统将在首次查询时初始化")


@app.on_event("shutdown")
def on_shutdown():
    logger.info("正在清理资源...")
    for component in [mastery_service, langgraph_engine]:
        if component and hasattr(component, 'close'):
            try:
                component.close()
            except Exception:
                pass
    logger.info("资源清理完成")


# ==================== 页面路由 ====================

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "integrated_index.html")


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)


# ==================== 核心查询 API ====================

@app.post("/api/query")
async def query(req: QueryRequest):
    engine = _require_engine()
    try:
        if req.session_id:
            session_id_var.set(req.session_id)
        logger.info("LangGraph 查询", session_id=req.session_id, question=req.question[:100])

        mastery_context = ""
        if req.session_id and mastery_service:
            mastery_context = mastery_service.build_mastery_context(req.session_id)

        response = await asyncio.to_thread(
            engine.query,
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
            entity_names = entity_service.get_all_entity_names() if entity_service else []
            extracted = entity_service.extract_entities_from_text(
                req.question + " " + result["answer"], entity_names
            ) if entity_service else []
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


@app.post("/api/query/stream")
async def query_stream(req: QueryRequest):
    """流式查询：SSE 逐 token 推送 + 工具状态 + 图谱数据"""
    engine = _require_engine()
    if req.session_id:
        session_id_var.set(req.session_id)

    mastery_context = ""
    if req.session_id and mastery_service:
        mastery_context = mastery_service.build_mastery_context(req.session_id)

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


# ==================== 会话记忆 API ====================

@app.get("/api/sessions")
async def list_sessions():
    engine = _require_engine()
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


@app.post("/api/sessions/new")
async def new_session():
    engine = _require_engine()
    session_id = engine.new_session()
    return {"session_id": session_id}


@app.get("/api/sessions/{session_id}/history")
async def session_history(session_id: str):
    engine = _require_engine()
    history = engine.get_session_history(session_id)
    return {"session_id": session_id, "history": history}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    engine = _require_engine()
    success = engine.delete_session(session_id)
    if success:
        return {"success": True}
    raise HTTPException(status_code=404, detail="会话不存在")


# ==================== 掌握状态 API ====================

@app.get("/api/entities")
async def get_all_entities():
    engine = _require_engine()
    try:
        return entity_service.get_entities_by_layer()
    except Exception as e:
        logger.error("获取实体列表失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/mastery/{session_id}")
async def get_mastery(session_id: str):
    if not mastery_service or not mastery_service.available:
        raise HTTPException(status_code=503, detail="掌握追踪器未初始化")
    return mastery_service.get_mastery(session_id)


@app.post("/api/mastery")
async def set_mastery(req: MasteryRequest):
    if not mastery_service or not mastery_service.available:
        raise HTTPException(status_code=503, detail="掌握追踪器未初始化")
    mastery_service.set_mastery(req.session_id, req.entity_name, req.mastered)
    return {"success": True}


@app.post("/api/mastery/batch")
async def set_mastery_batch(req: MasteryBatchRequest):
    if not mastery_service or not mastery_service.available:
        raise HTTPException(status_code=503, detail="掌握追踪器未初始化")
    mastery_service.set_mastery_batch(req.session_id, req.entities)
    return {"success": True}


# ==================== 图谱辅助 API ====================

@app.get("/api/config")
async def get_config():
    return {
        "features": {
            "voice_input": True,
            "history": True,
            "export": True,
            "theme": True,
            "fullscreen": True,
            "session_memory": langgraph_engine is not None,
            "image_generation": image_generator is not None and image_generator.is_available(),
        },
        "limits": {"max_query_length": 2000, "max_history_items": 20},
    }


@app.get("/api/graph_stats")
async def graph_stats():
    engine = _require_engine()
    try:
        return engine.stats_service.get_stats()
    except Exception as e:
        logger.error("获取统计信息失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/node_neighbors/{node_name}")
async def node_neighbors(node_name: str):
    engine = _require_engine()
    try:
        neighbors = engine.entity_retriever.get_neighbors(node_name)
        return {"neighbors": neighbors}
    except Exception as e:
        logger.error("获取节点邻居失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/search_nodes")
async def search_nodes(q: str = ""):
    engine = _require_engine()
    try:
        nodes = engine.entity_retriever.keyword_search(q, limit=20) if q else []
        return {"nodes": nodes}
    except Exception as e:
        logger.error("搜索节点失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/export_graph")
async def export_graph(req: ExportRequest):
    engine = _require_engine()
    try:
        graph_data = engine.entity_retriever.get_subgraph_by_query(req.query, limit=50)
        return {"graph_data": graph_data, "export_time": datetime.now().isoformat()}
    except Exception as e:
        logger.error("导出图谱失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "langgraph_engine_initialized": langgraph_engine is not None,
    }


@app.post("/api/generate_image")
async def generate_image(req: GenerateImageRequest):
    global image_generator
    if not image_generator:
        image_generator = init_image_generator()
    if not image_generator or not image_generator.is_available():
        raise HTTPException(status_code=503, detail="图片生成服务不可用，请安装 Graphviz（pip install graphviz 并安装系统软件）")
    try:
        result = image_generator.generate(req.graph_data, req.question)
        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("图片生成失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 刷题测验 API ====================

@app.get("/quiz", response_class=HTMLResponse)
async def quiz_page(request: Request):
    return templates.TemplateResponse(request, "quiz.html")


@app.get("/review", response_class=HTMLResponse)
async def review_page(request: Request):
    return templates.TemplateResponse(request, "review.html")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    return templates.TemplateResponse(request, "dashboard.html")


@app.get("/trial", response_class=HTMLResponse)
async def trial_page(request: Request):
    return templates.TemplateResponse(request, "trial.html")


@app.get("/api/quiz/questions")
async def get_quiz_questions(
    layer: Optional[str] = None,
    difficulty: Optional[str] = None,
    chapter: Optional[str] = None,
    limit: int = 20,
):
    global quiz_service
    if not quiz_service:
        if langgraph_engine:
            init_quiz_service()
        if not quiz_service:
            raise HTTPException(status_code=503, detail="Quiz 服务未初始化")
    try:
        return quiz_service.get_questions(
            layer=layer, difficulty=difficulty, chapter=chapter, limit=limit
        )
    except Exception as e:
        logger.error("获取测验题目失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/quiz/chapters")
async def get_chapter_structure():
    global quiz_service
    if not quiz_service:
        if langgraph_engine:
            init_quiz_service()
        if not quiz_service:
            raise HTTPException(status_code=503, detail="Quiz 服务未初始化")
    try:
        return quiz_service.get_chapter_structure()
    except Exception as e:
        logger.error("获取章节结构失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/quiz/answer")
async def submit_quiz_answer(req: QuizAnswerRequest):
    global quiz_service
    if not quiz_service:
        if langgraph_engine:
            init_quiz_service()
        if not quiz_service:
            raise HTTPException(status_code=503, detail="Quiz 服务未初始化")
    try:
        result = quiz_service.check_answer(req.question_id)
        if not result:
            raise HTTPException(status_code=404, detail="题目不存在")

        is_correct = req.selected_answer == result["correct_answer"]

        # 联动复习调度：答题结果驱动遗忘曲线更新，掌握度由曲线状态自动判定
        if req.session_id and mastery_service and mastery_service.available:
            refs = result.get("references", [])
            quality = 4 if is_correct else 1
            event_type = "quiz_correct" if is_correct else "quiz_wrong"
            chapter = result.get("chapter", "")
            for entity_name in refs:
                try:
                    mastery_service.record_review(
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


@app.get("/api/quiz/stats")
async def get_quiz_stats():
    global quiz_service
    if not quiz_service:
        if langgraph_engine:
            init_quiz_service()
        if not quiz_service:
            raise HTTPException(status_code=503, detail="Quiz 服务未初始化")
    try:
        return quiz_service.get_stats()
    except Exception as e:
        logger.error("获取测验统计失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/quiz/questions")
async def create_quiz_question(req: MCQuestionCreate):
    global quiz_service
    if not quiz_service:
        if langgraph_engine:
            init_quiz_service()
        if not quiz_service:
            raise HTTPException(status_code=503, detail="Quiz 服务未初始化")
    try:
        q_id = quiz_service.add_question(req.model_dump())
        return {"success": True, "question_id": q_id}
    except Exception as e:
        logger.error("创建题目失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/quiz/generate")
async def generate_quiz_questions(req: QuizGenerateRequest):
    global question_generator
    if not question_generator:
        if langgraph_engine:
            init_question_generator()
        if not question_generator:
            raise HTTPException(status_code=503, detail="习题生成服务未初始化")
    try:
        # LLM 生成耗时 5-20s，放线程池避免阻塞事件循环
        result = await asyncio.to_thread(
            question_generator.generate,
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


# ==================== 考试试卷 API ====================

@app.get("/api/exam/papers")
async def get_exam_papers():
    global exam_service
    if not exam_service:
        if langgraph_engine:
            init_exam_service()
        if not exam_service:
            raise HTTPException(status_code=503, detail="Exam 服务未初始化")
    try:
        return exam_service.get_exam_papers()
    except Exception as e:
        logger.error("获取试卷列表失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/exam/papers/{paper_id}")
async def get_exam_paper(paper_id: str):
    global exam_service
    if not exam_service:
        if langgraph_engine:
            init_exam_service()
        if not exam_service:
            raise HTTPException(status_code=503, detail="Exam 服务未初始化")
    try:
        paper = exam_service.get_exam_paper(paper_id)
        if not paper:
            raise HTTPException(status_code=404, detail="试卷不存在")
        return paper
    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取试卷详情失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/exam/papers/import")
async def import_exam_paper(request: Request):
    global exam_service
    if not exam_service:
        if langgraph_engine:
            init_exam_service()
        if not exam_service:
            raise HTTPException(status_code=503, detail="Exam 服务未初始化")
    try:
        data = await request.json()
        paper_id = exam_service.create_exam_paper(data)
        return {"success": True, "paper_id": paper_id}
    except Exception as e:
        logger.error("导入试卷失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/exam/submit")
async def submit_exam(req: ExamSubmitRequest):
    global exam_service
    if not exam_service:
        if langgraph_engine:
            init_exam_service()
        if not exam_service:
            raise HTTPException(status_code=503, detail="Exam 服务未初始化")
    try:
        result = exam_service.submit_exam(
            req.session_id, req.paper_id, req.answers
        )
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])

        # 附加 LLM 分析
        wrong_details = [d for d in result.get("details", []) if not d.get("correct")]
        analysis = exam_service.analyze_weaknesses(
            session_id=req.session_id, wrong_details=wrong_details
        )
        result["analysis"] = analysis

        # 附加错误统计
        if req.session_id:
            result["error_stats"] = exam_service.get_exam_error_stats(req.session_id)

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("提交试卷失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/exam/analysis/{session_id}")
async def get_exam_analysis(session_id: str):
    global exam_service
    if not exam_service:
        if langgraph_engine:
            init_exam_service()
        if not exam_service:
            raise HTTPException(status_code=503, detail="Exam 服务未初始化")
    try:
        analysis = exam_service.analyze_weaknesses(session_id)
        return analysis
    except Exception as e:
        logger.error("获取分析失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/exam/recommendations/{session_id}")
async def get_exam_recommendations(session_id: str, limit: int = 20):
    global exam_service
    if not exam_service:
        if langgraph_engine:
            init_exam_service()
        if not exam_service:
            raise HTTPException(status_code=503, detail="Exam 服务未初始化")
    try:
        return exam_service.get_recommendations(session_id, limit=limit)
    except Exception as e:
        logger.error("获取推荐失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/exam/history/{session_id}")
async def get_exam_history(session_id: str):
    global exam_service
    if not exam_service:
        if langgraph_engine:
            init_exam_service()
        if not exam_service:
            raise HTTPException(status_code=503, detail="Exam 服务未初始化")
    try:
        return exam_service.get_exam_history(session_id)
    except Exception as e:
        logger.error("获取考试历史失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/exam/result/{result_id}")
async def get_exam_result(result_id: int):
    global exam_service
    if not exam_service:
        if langgraph_engine:
            init_exam_service()
        if not exam_service:
            raise HTTPException(status_code=503, detail="Exam 服务未初始化")
    try:
        result = exam_service.get_exam_result(result_id)
        if not result:
            raise HTTPException(status_code=404, detail="考试记录不存在")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取考试结果失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 复习调度 API ====================

@app.get("/api/review/state/{session_id}")
async def get_review_state(session_id: str):
    if not mastery_service or not mastery_service.available:
        raise HTTPException(status_code=503, detail="复习调度器未初始化")
    try:
        return mastery_service.get_review_state(session_id)
    except Exception as e:
        logger.error("获取复习状态失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/review/due/{session_id}")
async def get_due_reviews(session_id: str, lookahead: float = 48, limit: int = 50):
    if not mastery_service or not mastery_service.available:
        raise HTTPException(status_code=503, detail="复习调度器未初始化")
    try:
        return mastery_service.get_due_reviews(session_id, lookahead, limit)
    except Exception as e:
        logger.error("获取到期复习失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/review/record")
async def record_review(req: ReviewRecordRequest):
    if not mastery_service or not mastery_service.available:
        raise HTTPException(status_code=503, detail="复习调度器未初始化")
    try:
        result = mastery_service.record_review(
            req.session_id, req.entity_name, req.quality, req.event_type
        )
        return result
    except Exception as e:
        logger.error("记录复习失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/review/stats/{session_id}")
async def get_review_stats(session_id: str):
    if not mastery_service or not mastery_service.available:
        raise HTTPException(status_code=503, detail="复习调度器未初始化")
    try:
        return mastery_service.get_review_stats(session_id)
    except Exception as e:
        logger.error("获取复习统计失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 答案满意度反馈 API ====================

@app.post("/api/feedback")
async def submit_feedback(req: FeedbackRequest):
    if not mastery_service or not mastery_service.available:
        raise HTTPException(status_code=503, detail="反馈服务未初始化")
    try:
        row_id = mastery_service.record_feedback(
            req.session_id or "", req.rating, req.question, req.comment
        )
        return {"success": True, "id": row_id}
    except Exception as e:
        logger.error("保存反馈失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/feedback/stats")
async def get_feedback_stats(session_id: Optional[str] = None):
    if not mastery_service or not mastery_service.available:
        raise HTTPException(status_code=503, detail="反馈服务未初始化")
    try:
        return mastery_service.get_feedback_stats(session_id)
    except Exception as e:
        logger.error("获取反馈统计失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 学习仪表盘 API ====================

@app.get("/api/dashboard/overview/{session_id}")
async def get_dashboard_overview(session_id: str):
    global dashboard_service
    if not dashboard_service:
        init_dashboard_service()
    if not dashboard_service:
        raise HTTPException(status_code=503, detail="仪表盘服务未初始化")
    try:
        return dashboard_service.get_overview(session_id)
    except Exception as e:
        logger.error("获取仪表盘数据失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/dashboard/report/{session_id}")
async def generate_dashboard_report(session_id: str):
    global dashboard_service
    if not dashboard_service:
        init_dashboard_service()
    if not dashboard_service:
        raise HTTPException(status_code=503, detail="仪表盘服务未初始化")
    try:
        report = dashboard_service.generate_report(session_id)
        return {"report": report}
    except Exception as e:
        logger.error("生成学习报告失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 错题集 & 已做题目 本地存储 API ====================

class WrongEntry(BaseModel):
    id: str
    text: str
    option_a: str
    option_b: str
    option_c: str
    option_d: str
    correct_answer: str
    explanation: str
    layer: str
    difficulty: str
    selected: str
    time: Optional[str] = None


@app.get("/api/quiz/done")
async def api_get_done_ids():
    return {"ids": quiz_storage.get_done_ids()}


@app.post("/api/quiz/done/{question_id}")
async def api_mark_done(question_id: str):
    return {"ids": quiz_storage.mark_done(question_id)}


@app.get("/api/quiz/wrong")
async def api_get_wrong_list():
    return {"list": quiz_storage.get_wrong_list()}


@app.post("/api/quiz/wrong")
async def api_add_wrong(entry: WrongEntry):
    return {"list": quiz_storage.add_wrong(entry.model_dump())}


@app.delete("/api/quiz/wrong/{question_id}")
async def api_remove_wrong(question_id: str):
    return {"list": quiz_storage.remove_wrong(question_id)}


@app.delete("/api/quiz/wrong")
async def api_clear_wrong():
    quiz_storage.clear_wrong()
    return {"success": True}


# ==================== 前测-后测试用评估 API ====================

def _ensure_trial_service():
    global trial_service
    if not trial_service:
        if langgraph_engine:
            init_trial_service()
        if not trial_service:
            raise HTTPException(status_code=503, detail="Trial 服务未初始化")
    return trial_service


@app.post("/api/trial/create")
async def create_trial(req: TrialCreateRequest):
    try:
        service = _ensure_trial_service()
        return service.create_trial(req.name, per_layer=req.per_layer, seed=req.seed)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("创建试用批次失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/trial/list")
async def list_trials():
    try:
        return {"trials": _ensure_trial_service().list_trials()}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取试用批次列表失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/trial/{trial_id}/participants")
async def add_trial_participant(trial_id: str, req: TrialParticipantRequest):
    try:
        participant = _ensure_trial_service().add_participant(trial_id, req.code)
        return participant
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("登记被试失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/trial/{trial_id}/participants")
async def list_trial_participants(trial_id: str):
    try:
        return {"participants": _ensure_trial_service().list_participants(trial_id)}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("获取被试列表失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/trial/join")
async def join_trial(req: TrialJoinRequest):
    try:
        return _ensure_trial_service().join(req.code, req.session_id)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("被试加入失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/trial/test/start")
async def start_trial_test(req: TrialTestStartRequest):
    try:
        return _ensure_trial_service().start_test(req.code, req.phase)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("开始测试失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/trial/test/submit")
async def submit_trial_test(req: TrialTestSubmitRequest):
    try:
        return _ensure_trial_service().submit_test(req.test_id, req.answers)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("提交测试失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/trial/{trial_id}/analysis")
async def get_trial_analysis(trial_id: str):
    try:
        return _ensure_trial_service().get_analysis(trial_id)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("获取试用分析失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/trial/{trial_id}/report")
async def generate_trial_report(trial_id: str):
    try:
        report = _ensure_trial_service().generate_report(trial_id)
        if report is None:
            raise HTTPException(status_code=502, detail="报告生成失败，请稍后重试")
        return {"trial_id": trial_id, "report": report}
    except HTTPException:
        raise
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("生成试用评估报告失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 启动入口 ====================

def main():
    import uvicorn
    s = get_settings()
    logger.info("启动 GraphRAG Web 应用", host=s.web_host, port=s.web_port, debug=s.debug)
    uvicorn.run(
        "web.graph_rag_web:app",
        host=s.web_host,
        port=s.web_port,
        reload=s.debug,
    )


if __name__ == '__main__':
    main()
