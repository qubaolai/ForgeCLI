"""进程握手: 一次性启动令牌换 cookie, 存活探测, 前端启动所需的最小事实。

这三条不属于任何一个资源, 也不需要激活项目 —— ``/boot`` 与 ``/health`` 更是在会话
cookie 建立之前就要能通 (见 ``security._OPEN_PATHS``)。
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from forgecli.interfaces.web.deps import registry
from forgecli.interfaces.web.security import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    SecurityState,
)
from forgecli.shared import __version__

router = APIRouter()


def _security(request: Request) -> SecurityState:
    state: SecurityState = request.app.state.security
    return state


@router.get("/boot", include_in_schema=False)
async def boot(token: str, request: Request) -> Response:
    security = _security(request)
    if not secrets.compare_digest(token, security.boot_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "启动令牌无效")
    security.boot_token = ""
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE,
        security.session,
        httponly=True,
        samesite="strict",
        secure=False,
    )
    response.set_cookie(
        CSRF_COOKIE,
        security.csrf,
        httponly=False,
        samesite="strict",
        secure=False,
    )
    return response


@router.get("/api/v1/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/api/v1/bootstrap")
async def bootstrap(request: Request) -> dict[str, object]:
    runtime = registry(request).active
    return {
        "version": __version__,
        "active_project_id": (None if runtime is None else runtime.project.project_id),
        "csrf_token": _security(request).csrf,
    }
