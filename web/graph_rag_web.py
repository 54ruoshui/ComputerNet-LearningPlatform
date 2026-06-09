"""
GraphRAG Web 界面
基于 FastAPI 的 Web 应用，使用 LangGraph + LangChain 提供带会话记忆的知识图谱问答。
"""

import os
import re
import sys
import uuid
from datetime import datetime
from typing import Dict, List, Optional

import structlog
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from src.exceptions import GraphRAGError, ConfigError, ConnectionError_
from src.logging_config import setup_logging, new_request_id, request_id_var, session_id_var
from src.settings import get_settings

# 添加项目根目录到 Python 路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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

from web.middleware import APIKeyMiddleware
app.add_middleware(APIKeyMiddleware)


@app.middleware("http")
async def inject_request_id(request: Request, call_next):
    """每个请求注入 request_id 并记录到上下文"""
    rid = new_request_id()
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response


# 静态文件和模板
app.mount("/static", StaticFiles(directory=static_folder), name="static")
templates = Jinja2Templates(directory=template_folder)

os.makedirs(generated_images_dir, exist_ok=True)
app.mount("/generated_images", StaticFiles(directory=generated_images_dir), name="generated_images")

# ==================== 全局实例 ====================
langgraph_engine = None
image_generator = None
mastery_tracker = None
_all_entity_names: Optional[List[str]] = None


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


# ==================== 全局异常处理 ====================

@app.exception_handler(GraphRAGError)
async def graphrag_error_handler(request: Request, exc: GraphRAGError):
    """统一处理 GraphRAG 自定义异常"""
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
    global langgraph_engine
    try:
        logger.info("初始化 GraphRAG ReAct Agent...")
        from src.langchain_config import LangGraphRAGConfig
        from src.graphrag_agent import GraphRAGAgent
        config = LangGraphRAGConfig()
        langgraph_engine = GraphRAGAgent(config)
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


def _require_engine():
    """确保引擎已初始化，否则返回 503"""
    if not langgraph_engine:
        raise HTTPException(status_code=503, detail="LangGraph 引擎未初始化，请检查 Neo4j 和 API 配置")
    return langgraph_engine


def init_mastery_tracker():
    global mastery_tracker
    try:
        logger.info("初始化掌握追踪器...")
        from src.mastery_tracker import MasteryTracker
        db_path = os.path.join(project_root, "mastery.db")
        mastery_tracker = MasteryTracker(db_path)
        logger.info("掌握追踪器初始化完成")
        return mastery_tracker
    except Exception as e:
        logger.error("掌握追踪器初始化失败", error=str(e))
        mastery_tracker = None
        return None


def _get_all_entity_names() -> List[str]:
    global _all_entity_names
    if _all_entity_names is not None:
        return _all_entity_names
    engine = _require_engine()
    with engine.driver.session() as session:
        result = session.run("MATCH (e:Entity) WHERE e.name IS NOT NULL RETURN e.name AS name")
        _all_entity_names = sorted([r["name"] for r in result], key=len, reverse=True)
    return _all_entity_names


def _extract_entities_from_text(text: str, entity_names: List[str]) -> List[str]:
    found = []
    for name in entity_names:
        if re.search(r'(?<![a-zA-Z0-9])' + re.escape(name) + r'(?![a-zA-Z0-9])', text):
            found.append(name)
    return found


# ==================== 启动/关闭事件 ====================

@app.on_event("startup")
def on_startup():
    s = get_settings()
    if not s.fast_start:
        init_langgraph_engine()
        init_image_generator()
        init_mastery_tracker()
    else:
        logger.info("快速启动模式：系统将在首次查询时初始化")


@app.on_event("shutdown")
def on_shutdown():
    logger.info("正在清理资源...")
    for component in [mastery_tracker, langgraph_engine]:
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
    return JSONResponse(content={}, status_code=204)


# ==================== 核心查询 API ====================

@app.post("/api/query")
async def query(req: QueryRequest):
    """处理查询请求 — LangGraph 引擎（带会话记忆）"""
    engine = _require_engine()
    try:
        if req.session_id:
            session_id_var.set(req.session_id)
        logger.info("LangGraph 查询", session_id=req.session_id, question=req.question[:100])

        # 构建掌握上下文用于个性化回答
        mastery_context = ""
        if req.session_id and mastery_tracker:
            summary = mastery_tracker.get_mastery_summary(req.session_id)
            if summary and summary.get("total", 0) > 0:
                mastered_list = summary.get("mastered_names", [])[:15]
                unmastered_list = summary.get("unmastered_names", [])[:15]
                mastery_context = (
                    f"【学生知识掌握状态】已掌握：{'、'.join(mastered_list)}。"
                    f"未掌握：{'、'.join(unmastered_list)}。"
                    f"请在回答时对未掌握的知识点做更详细的解释。"
                )

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

        # 提取问答中涉及的知识点实体
        try:
            entity_names = _get_all_entity_names()
            extracted = _extract_entities_from_text(
                req.question + " " + result["answer"], entity_names
            )
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
        with engine.driver.session() as session:
            result = session.run("""
                MATCH (l:Layer)-[:CONTAINS]->(e:Entity)
                RETURN l.name AS layer, l.layer_number AS layer_num,
                       e.name AS name, e.entity_type AS entity_type, e.description AS description
                ORDER BY l.layer_number, e.entity_type, e.name
            """)
            entities_by_layer: Dict = {}
            total = 0
            for record in result:
                layer = record["layer"]
                if layer not in entities_by_layer:
                    entities_by_layer[layer] = []
                entities_by_layer[layer].append({
                    "name": record["name"],
                    "entity_type": record["entity_type"],
                    "description": record["description"] or "",
                })
                total += 1
        return {"entities": entities_by_layer, "total": total}
    except Exception as e:
        logger.error("获取实体列表失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/mastery/{session_id}")
async def get_mastery(session_id: str):
    if not mastery_tracker:
        raise HTTPException(status_code=503, detail="掌握追踪器未初始化")
    mastery = mastery_tracker.get_mastery(session_id)
    summary = mastery_tracker.get_mastery_summary(session_id)
    return {"mastery": mastery, "summary": summary}


@app.post("/api/mastery")
async def set_mastery(req: MasteryRequest):
    if not mastery_tracker:
        raise HTTPException(status_code=503, detail="掌握追踪器未初始化")
    mastery_tracker.set_mastery(req.session_id, req.entity_name, req.mastered)
    return {"success": True}


@app.post("/api/mastery/batch")
async def set_mastery_batch(req: MasteryBatchRequest):
    if not mastery_tracker:
        raise HTTPException(status_code=503, detail="掌握追踪器未初始化")
    for item in req.entities:
        mastery_tracker.set_mastery(req.session_id, item["name"], item["mastered"])
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
        return engine.get_stats()
    except Exception as e:
        logger.error("获取统计信息失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/node_neighbors/{node_name}")
async def node_neighbors(node_name: str):
    engine = _require_engine()
    try:
        neighbors = engine.retriever.get_neighbors(node_name, depth=2)
        return {"neighbors": neighbors}
    except Exception as e:
        logger.error("获取节点邻居失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/search_nodes")
async def search_nodes(q: str = ""):
    engine = _require_engine()
    if not q.strip():
        return {"nodes": []}
    try:
        nodes = engine.retriever.keyword_search(q, limit=20)
        return {"nodes": nodes}
    except Exception as e:
        logger.error("搜索节点失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/export_graph")
async def export_graph(req: ExportRequest):
    engine = _require_engine()
    try:
        graph_data = engine.retriever.get_subgraph_by_query(req.query, limit=50)
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
    """Graphviz 生成知识图谱可视化图片"""
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
