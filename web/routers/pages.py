"""页面路由"""

import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from web import deps
from web.deps import project_root

router = APIRouter()

templates = Jinja2Templates(directory=os.path.join(project_root, 'templates'))


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "integrated_index.html")


@router.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


@router.get("/quiz", response_class=HTMLResponse)
def quiz_page(request: Request):
    return templates.TemplateResponse(request, "quiz.html")


@router.get("/review", response_class=HTMLResponse)
def review_page(request: Request):
    return templates.TemplateResponse(request, "review.html")


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request):
    return templates.TemplateResponse(request, "dashboard.html")


@router.get("/trial", response_class=HTMLResponse)
def trial_page(request: Request):
    return templates.TemplateResponse(request, "trial.html")


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html")


@router.get("/teacher", response_class=HTMLResponse)
def teacher_page(request: Request):
    # 班级学情页：仅 teacher/admin 角色可见，其余跳登录（服务端强制，前端按钮仅是显隐）
    user = deps.resolve_user(request)
    if not user or user.get("role") not in ("teacher", "admin"):
        return RedirectResponse("/login?next=/teacher", status_code=302)
    return templates.TemplateResponse(request, "teacher.html")
