"""
GraphRAG Web 界面
基于 FastAPI 的 Web 应用，使用 LangGraph + LangChain 提供带会话记忆的知识图谱问答。
"""

import os
import sys
import json
import logging
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# 添加项目根目录到 Python 路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
template_folder = os.path.join(project_root, 'templates')
static_folder = os.path.join(project_root, 'static')
generated_images_dir = os.path.join(project_root, 'data', 'generated_images')

# FastAPI 实例
app = FastAPI(title="GraphRAG 知识图谱问答系统", version="2.0")

# 静态文件和模板
app.mount("/static", StaticFiles(directory=static_folder), name="static")
templates = Jinja2Templates(directory=template_folder)

# 挂载本地生成的图片目录
os.makedirs(generated_images_dir, exist_ok=True)
app.mount("/generated_images", StaticFiles(directory=generated_images_dir), name="generated_images")

# ==================== 全局实例 ====================
langgraph_engine = None
quality_monitor = None
image_generator = None


# ==================== Pydantic 请求模型 ====================

class QueryRequest(BaseModel):
    question: str
    session_id: Optional[str] = None


class ExportRequest(BaseModel):
    query: str


class GenerateImageRequest(BaseModel):
    graph_data: dict
    question: str


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
    except Exception as e:
        logger.error(f"GraphRAG ReAct Agent 初始化失败: {e}")
        import traceback
        traceback.print_exc()
        langgraph_engine = None
        return None


def init_quality_monitor():
    global quality_monitor
    try:
        logger.info("初始化质量监控器...")
        from src.quality_monitor import QualityMonitor
        quality_monitor = QualityMonitor(
            neo4j_uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            neo4j_auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "")),
        )
        logger.info("质量监控器初始化完成")
        return quality_monitor
    except Exception as e:
        logger.error(f"质量监控器初始化失败: {e}")
        quality_monitor = None
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
        logger.error(f"Graphviz 图片生成器初始化失败: {e}")
        image_generator = None
        return None


def _require_engine():
    """确保引擎已初始化，否则返回 503"""
    if not langgraph_engine:
        raise HTTPException(status_code=503, detail="LangGraph 引擎未初始化，请检查 Neo4j 和 API 配置")
    return langgraph_engine


# ==================== 启动/关闭事件 ====================

@app.on_event("startup")
def on_startup():
    fast_start = os.getenv('FAST_START', 'false').lower() == 'true'
    if not fast_start:
        init_langgraph_engine()
        init_quality_monitor()
        init_image_generator()
    else:
        logger.info("快速启动模式：系统将在首次查询时初始化")


@app.on_event("shutdown")
def on_shutdown():
    logger.info("正在清理资源...")
    for component in [quality_monitor, langgraph_engine]:
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


# ==================== 核心查询 API ====================

@app.post("/api/query")
async def query(req: QueryRequest):
    """处理查询请求 — LangGraph 引擎（带会话记忆）"""
    engine = _require_engine()
    try:
        logger.info(f"LangGraph 查询 [会话: {req.session_id}]: {req.question}")
        response = engine.query(req.question, session_id=req.session_id)

        result = {
            "question": response.get("question", req.question),
            "answer": response.get("answer", "未获取到答案"),
            "processing_time": response.get("processing_time", 0),
            "timestamp": datetime.now().isoformat(),
            "session_id": response.get("session_id"),
        }
        if response.get("context_length"):
            result["context_length"] = response["context_length"]
        if response.get("graph_data"):
            result["graph_data"] = response["graph_data"]

        # 收集质量指标
        if quality_monitor:
            try:
                quality_monitor.collect_metrics(
                    extraction_results={"keywords": [], "relationships": []},
                    validation_results={"total_validations": 0, "validity_rate": 0.8, "average_confidence": 0.8},
                    performance_data={
                        "response_time": result.get("processing_time", 0),
                        "error_rate": 0.0,
                    },
                )
            except Exception as qe:
                logger.debug(f"质量指标收集失败（不影响查询）: {qe}")

        return JSONResponse(content=result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"查询处理失败: {e}")
        if quality_monitor:
            try:
                quality_monitor.collect_metrics(
                    extraction_results={"keywords": [], "relationships": []},
                    validation_results={"total_validations": 0, "validity_rate": 0.0, "average_confidence": 0.0},
                    performance_data={"response_time": 0, "error_rate": 1.0},
                )
            except Exception:
                pass
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
        logger.error(f"列出会话失败: {e}")
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
            "quality_monitoring": quality_monitor is not None,
            "session_memory": langgraph_engine is not None,
            "image_generation": image_generator is not None and image_generator.is_available(),
        },
        "limits": {"max_query_length": 1000, "max_history_items": 20},
    }


@app.get("/api/graph_stats")
async def graph_stats():
    engine = _require_engine()
    try:
        return engine.get_stats()
    except Exception as e:
        logger.error(f"获取统计信息失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/node_neighbors/{node_name}")
async def node_neighbors(node_name: str):
    engine = _require_engine()
    try:
        neighbors = engine.retriever.get_neighbors(node_name, depth=2)
        return {"neighbors": neighbors}
    except Exception as e:
        logger.error(f"获取节点邻居失败: {e}")
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
        logger.error(f"搜索节点失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/export_graph")
async def export_graph(req: ExportRequest):
    engine = _require_engine()
    try:
        graph_data = engine.retriever.get_subgraph_by_query(req.query, limit=50)
        return {"graph_data": graph_data, "export_time": datetime.now().isoformat()}
    except Exception as e:
        logger.error(f"导出图谱失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "langgraph_engine_initialized": langgraph_engine is not None,
        "quality_monitor_enabled": quality_monitor is not None,
    }


@app.get("/api/quality_report")
async def quality_report():
    if not quality_monitor:
        raise HTTPException(status_code=503, detail="质量监控器未启用")
    try:
        return quality_monitor.get_quality_report()
    except Exception as e:
        logger.error(f"获取质量报告失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/quality_alerts")
async def quality_alerts():
    if not quality_monitor:
        raise HTTPException(status_code=503, detail="质量监控器未启用")
    try:
        alerts = quality_monitor.get_active_alerts()
        return {"alerts": [a.__dict__ for a in alerts], "count": len(alerts)}
    except Exception as e:
        logger.error(f"获取质量告警失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/quality_trends")
async def quality_trends(period: str = "day"):
    if not quality_monitor:
        raise HTTPException(status_code=503, detail="质量监控器未启用")
    try:
        trends = quality_monitor.analyze_trends(period=period)
        return {"period": period, "trends": [t.__dict__ for t in trends], "count": len(trends)}
    except Exception as e:
        logger.error(f"获取质量趋势失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
        logger.error(f"图片生成失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 启动入口 ====================

def main():
    import uvicorn
    host = os.getenv('WEB_HOST', '0.0.0.0')
    port = int(os.getenv('WEB_PORT', '5000'))
    debug = os.getenv('DEBUG', 'false').lower() == 'true'

    logger.info(f"启动 GraphRAG Web 应用 (FastAPI + LangGraph + 记忆)")
    logger.info(f"监听地址: http://{host}:{port}")
    logger.info(f"调试模式: {'启用' if debug else '禁用'}")
    logger.info("会话记忆: 已启用 (MemorySaver)")
    logger.info(f"API 文档: http://{host}:{port}/docs")

    uvicorn.run(
        "web.graph_rag_web:app",
        host=host,
        port=port,
        reload=debug,
    )


if __name__ == '__main__':
    main()
