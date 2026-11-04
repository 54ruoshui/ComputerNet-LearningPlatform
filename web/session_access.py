"""会话归属校验（按访客 cookie）— query/quiz/mastery/exam/dashboard 用户数据路由共用"""

import structlog
from fastapi import HTTPException, Request

from web import deps

logger = structlog.get_logger(__name__)

# 旧版共享/匿名兜底会话：无个人数据隔离意义，绑定会互相顶掉，跳过归属校验
SHARED_SESSION_IDS = {"", "default"}


def visitor_id(request: Request) -> str | None:
    vid = request.scope.get("aigc_visitor")
    if vid:
        return vid
    return request.cookies.get("aigc_visitor")


def bind_session(session_id: str, request: Request):
    """把会话绑定到当前访客（best-effort；tracker 不可用时静默跳过）"""
    tracker = deps._get_tracker()
    visitor = visitor_id(request)
    if tracker and visitor:
        try:
            tracker.bind_session(session_id, visitor)
        except Exception as e:
            logger.warning("会话归属绑定失败", session_id=session_id, error=str(e))


def ensure_session_owner(session_id: str | None, request: Request) -> bool:
    """归属校验：未绑定会话就地懒绑定（治愈存量会话），绑定他人返回 False。

    tracker/访客不可用时放行（fail-open，与其余 mastery 降级行为一致）。
    """
    if not session_id or session_id in SHARED_SESSION_IDS:
        return True
    tracker = deps._get_tracker()
    visitor = visitor_id(request)
    if not tracker or not visitor:
        return True
    owner = tracker.get_session_owner(session_id)
    if owner is None:
        bind_session(session_id, request)
        return True
    return owner == visitor


def require_session_owner(session_id: str | None, request: Request):
    """非本人会话一律 404（不向调用方暴露他人会话是否存在）"""
    if not ensure_session_owner(session_id, request):
        raise HTTPException(status_code=404, detail="会话不存在")


def session_accessible(session_id: str, request: Request) -> bool:
    """history/delete 的归属校验：未绑定或绑定他人一律视为不存在（fail-open 同上）"""
    tracker = deps._get_tracker()
    visitor = visitor_id(request)
    if not tracker or not visitor:
        return True
    owner = tracker.get_session_owner(session_id)
    return owner is not None and owner == visitor
