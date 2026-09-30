"""图谱辅助 API：实体/统计/邻居/搜索/导出/图片生成"""

from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException

from web import deps
from web.deps import get_image_generator, get_langgraph_engine
from web.schemas import ExportRequest, GenerateImageRequest

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api", tags=["graph"])


@router.get("/entities")
def get_all_entities(engine=Depends(get_langgraph_engine)):
    try:
        return deps.entity_service.get_entities_by_layer()
    except Exception as e:
        logger.error("获取实体列表失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/graph_stats")
def graph_stats(engine=Depends(get_langgraph_engine)):
    try:
        return engine.stats_service.get_stats()
    except Exception as e:
        logger.error("获取统计信息失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/node_neighbors/{node_name}")
def node_neighbors(node_name: str, engine=Depends(get_langgraph_engine)):
    try:
        neighbors = engine.entity_retriever.get_neighbors(node_name)
        return {"neighbors": neighbors}
    except Exception as e:
        logger.error("获取节点邻居失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search_nodes")
def search_nodes(q: str = "", engine=Depends(get_langgraph_engine)):
    try:
        nodes = engine.entity_retriever.keyword_search(q, limit=20) if q else []
        return {"nodes": nodes}
    except Exception as e:
        logger.error("搜索节点失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/export_graph")
def export_graph(req: ExportRequest, engine=Depends(get_langgraph_engine)):
    try:
        graph_data = engine.entity_retriever.get_subgraph_by_query(req.query, limit=50)
        return {"graph_data": graph_data, "export_time": datetime.now().isoformat()}
    except Exception as e:
        logger.error("导出图谱失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate_image")
def generate_image(req: GenerateImageRequest, generator=Depends(get_image_generator)):
    try:
        result = generator.generate(req.graph_data, req.question)
        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("图片生成失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
