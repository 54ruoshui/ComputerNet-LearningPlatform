"""账号认证 API — 注册 / 登录 / 登出 / 当前用户（可选登录，游客照常使用）"""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from src.services.auth_service import (
    AccountLockedError,
    EmailExistsError,
    InvalidCredentialsError,
)
from web import deps
from web.deps import get_auth_service
from web.schemas import LoginRequest, RegisterRequest, SessionClaimRequest

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

USER_COOKIE = "aigc_user"
COOKIE_MAX_AGE = 30 * 24 * 3600


def _set_user_cookie(response: Response, token: str):
    # 不加 Secure：教室部署为 http 内网，与 aigc_visitor cookie 策略一致
    response.set_cookie(
        USER_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        max_age=COOKIE_MAX_AGE,
        path="/",
    )


def _visitor(request: Request) -> str | None:
    vid = request.scope.get("aigc_visitor")
    if vid:
        return vid
    return request.cookies.get("aigc_visitor")


@router.post("/register", status_code=201)
def register(req: RegisterRequest, request: Request, response: Response, service=Depends(get_auth_service)):
    try:
        user = service.register(req.email, req.password, req.nickname)
        # 注册即登录（发 token + 接续当前会话），减少教室使用摩擦
        token = service.issue_token(user["id"])
        session_id = req.session_id
        if session_id and session_id != "default":
            service.attach_session(user["id"], session_id)
        if session_id:
            service.claim_user_session(user["id"], session_id, _visitor(request))
        _set_user_cookie(response, token)
        return {"user": user, "session_id": session_id}
    except EmailExistsError:
        raise HTTPException(status_code=409, detail="该邮箱已注册，请直接登录")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("注册失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/login")
def login(req: LoginRequest, request: Request, response: Response, service=Depends(get_auth_service)):
    try:
        result = service.login(
            req.email,
            req.password,
            visitor_id=_visitor(request),
            current_session_id=req.session_id,
        )
        _set_user_cookie(response, result["token"])
        return {"user": result["user"], "session_id": result["session_id"]}
    except InvalidCredentialsError:
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    except AccountLockedError as e:
        raise HTTPException(
            status_code=429,
            detail=str(e),
            headers={"Retry-After": str(e.retry_seconds)},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("登录失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(USER_COOKIE)
    if token and deps.auth_service:
        try:
            deps.auth_service.logout(token)
        except Exception as e:
            logger.warning("登出清理失败", error=str(e))
    response.delete_cookie(USER_COOKIE, path="/")
    return {"success": True}


@router.get("/me")
def me(request: Request):
    # 游客也是正常态（可选登录）：返回 200 + user=null，供前端 auth-bar 单形态判断
    user = deps.resolve_user(request)
    return {"user": user}


@router.post("/sessions/claim")
def claim_session(req: SessionClaimRequest, request: Request, service=Depends(get_auth_service)):
    """切换到本账号名下的某个会话：改绑到当前访客后由前端写 localStorage 并刷新"""
    user = deps.resolve_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    try:
        if not service.claim_user_session(user["id"], req.session_id, _visitor(request)):
            raise HTTPException(status_code=404, detail="会话不存在")
        return {"session_id": req.session_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("会话切换失败", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
