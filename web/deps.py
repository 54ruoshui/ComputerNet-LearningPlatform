"""服务装配与依赖注入。

持有全局服务实例（测试通过直接给本模块属性赋值注入 mock）、
init_* 初始化函数与 get_* FastAPI 依赖（懒初始化 + double-checked locking）。
"""

import os
import secrets
import threading

import structlog
from fastapi import Header, HTTPException, Request

from src.exceptions import GraphRAGError
from src.settings import get_settings

logger = structlog.get_logger(__name__)

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ==================== 全局实例（测试注入点） ====================

langgraph_engine = None
image_generator = None
entity_service = None
mastery_service = None
quiz_service = None
exam_service = None
dashboard_service = None
question_generator = None
trial_service = None
auth_service = None

_init_lock = threading.Lock()


def _get_shared_driver():
    """优先复用 langgraph_engine 的 Neo4j driver，否则新建。"""
    if langgraph_engine and hasattr(langgraph_engine, "driver"):
        return langgraph_engine.driver
    from neo4j import GraphDatabase
    s = get_settings()
    return GraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password))


def _get_tracker():
    """从 MasteryService 门面取底层 MasteryTracker，不可用时返回 None。"""
    if mastery_service and mastery_service.available:
        return mastery_service._tracker
    return None


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
        try:
            # quiz_storage 时代的全局 JSON 一次性迁入 default 会话（幂等）
            tracker.migrate_legacy_quiz_json(os.path.join(project_root, "data", "quiz"))
        except Exception as e:
            logger.warning("旧 quiz JSON 迁移失败", error=str(e))
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
        quiz_service = QuizService(_get_shared_driver())
        quiz_service.create_indexes()
        logger.info("QuizService 初始化完成")
    except Exception as e:
        logger.error("QuizService 初始化失败", error=str(e))
        quiz_service = None


def init_question_generator():
    global question_generator
    try:
        from src.services.question_generator import QuestionGenerator
        question_generator = QuestionGenerator(_get_shared_driver())
        logger.info("QuestionGenerator 初始化完成")
    except Exception as e:
        logger.error("QuestionGenerator 初始化失败", error=str(e))
        question_generator = None


def init_exam_service():
    global exam_service
    try:
        from src.services.exam_service import ExamService
        exam_service = ExamService(_get_shared_driver(), _get_tracker())
        logger.info("ExamService 初始化完成")
    except Exception as e:
        logger.error("ExamService 初始化失败", error=str(e))
        exam_service = None


def init_dashboard_service():
    global dashboard_service
    try:
        from src.services.dashboard_service import DashboardService
        dashboard_service = DashboardService(_get_shared_driver(), _get_tracker(), quiz_service)
        logger.info("DashboardService 初始化完成")
    except Exception as e:
        logger.error("DashboardService 初始化失败", error=str(e))
        dashboard_service = None


def init_trial_service():
    global trial_service
    try:
        from src.services.trial_service import TrialService
        tracker = _get_tracker()
        if tracker is None:
            # TrialService 的全部功能依赖 SQLite 存储，无 tracker 视为初始化失败
            raise RuntimeError("MasteryTracker 不可用，TrialService 无法初始化")
        trial_service = TrialService(_get_shared_driver(), tracker)
        logger.info("TrialService 初始化完成")
    except Exception as e:
        logger.error("TrialService 初始化失败", error=str(e))
        trial_service = None


def init_auth_service():
    global auth_service
    try:
        from src.services.auth_service import AuthService
        s = get_settings()
        auth_service = AuthService(
            db_path=os.path.join(project_root, "data", "memory", "mastery.db"),
            tracker=_get_tracker(),
            token_days=s.auth_token_days,
            max_failures=s.auth_max_login_failures,
            lockout_minutes=s.auth_lockout_minutes,
            teacher_code=s.teacher_code,
            admin_code=s.admin_code,
        )
        logger.info("AuthService 初始化完成")
    except Exception as e:
        logger.error("AuthService 初始化失败", error=str(e))
        auth_service = None


# ==================== FastAPI 依赖（懒初始化语义与拆分前各路由一致） ====================

def require_admin(request: Request, x_admin_token: str = Header(default="")):
    """管理端点双通道校验：admin 角色登录 OR X-Admin-Token（研究者脚本/页面 token 流兼容）。

    登录但非 admin → 403；未登录且无有效 token → 401；未配置 ADMIN_TOKEN 时 token 通道
    fail-closed（admin 角色登录通道不受影响）。
    """
    user = resolve_user(request)
    if user is not None and user.get("role") == "admin":
        return user
    token = get_settings().admin_token
    if token and secrets.compare_digest(x_admin_token, token):
        return {"role": "admin", "nickname": "研究者"}
    if user is not None:
        raise HTTPException(status_code=403, detail="该操作需要管理员权限")
    raise HTTPException(status_code=401, detail="需要管理口令，请在请求头中设置 X-Admin-Token")


def require_teacher(request: Request):
    """教师端点校验：teacher 或 admin 角色。未登录 401、学生 403。"""
    user = resolve_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    if user.get("role") not in ("teacher", "admin"):
        raise HTTPException(status_code=403, detail="该操作需要教师或管理员权限")
    return user


def get_langgraph_engine():
    if not langgraph_engine:
        raise HTTPException(status_code=503, detail="LangGraph 引擎未初始化，请检查 Neo4j 和 API 配置")
    return langgraph_engine


def get_mastery_service():
    """不懒初始化，可能返回 None；可用性判断由各路由自定 503 文案。"""
    return mastery_service


def get_quiz_service():
    if not quiz_service:
        with _init_lock:
            if not quiz_service and langgraph_engine:
                init_quiz_service()
    if not quiz_service:
        raise HTTPException(status_code=503, detail="Quiz 服务未初始化")
    return quiz_service


def get_question_generator():
    if not question_generator:
        with _init_lock:
            if not question_generator and langgraph_engine:
                init_question_generator()
    if not question_generator:
        raise HTTPException(status_code=503, detail="习题生成服务未初始化")
    return question_generator


def get_exam_service():
    if not exam_service:
        with _init_lock:
            if not exam_service and langgraph_engine:
                init_exam_service()
    if not exam_service:
        raise HTTPException(status_code=503, detail="Exam 服务未初始化")
    return exam_service


def get_dashboard_service():
    if not dashboard_service:
        with _init_lock:
            if not dashboard_service:
                init_dashboard_service()
    if not dashboard_service:
        raise HTTPException(status_code=503, detail="仪表盘服务未初始化")
    return dashboard_service


def get_trial_service():
    if not trial_service:
        with _init_lock:
            if not trial_service and langgraph_engine:
                init_trial_service()
    if not trial_service:
        raise HTTPException(status_code=503, detail="Trial 服务未初始化")
    return trial_service


def get_auth_service():
    # 不依赖 langgraph_engine（auth 只需 SQLite，tracker 缺失时服务自身降级）
    if not auth_service:
        with _init_lock:
            if not auth_service:
                init_auth_service()
    if not auth_service:
        raise HTTPException(status_code=503, detail="账号服务未初始化")
    return auth_service


def resolve_user(request) -> dict | None:
    """从 aigc_user cookie 解析当前登录用户；游客/异常一律 None（auth 是可选叠加层）"""
    if not auth_service:
        return None
    token = request.cookies.get("aigc_user")
    return auth_service.resolve_user(token)


def get_image_generator():
    if not image_generator:
        with _init_lock:
            if not image_generator:
                init_image_generator()
    if not image_generator or not image_generator.is_available():
        raise HTTPException(status_code=503, detail="图片生成服务不可用，请安装 Graphviz（pip install graphviz 并安装系统软件）")
    return image_generator


# ==================== 生命周期 ====================

def shutdown():
    logger.info("正在清理资源...")
    for component in [mastery_service, auth_service, langgraph_engine]:
        if component and hasattr(component, 'close'):
            try:
                component.close()
            except Exception:
                pass
    logger.info("资源清理完成")
