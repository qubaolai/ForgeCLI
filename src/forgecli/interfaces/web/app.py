"""本地 Web 控制面的组装 (ADR-0025 决策 1)。

这个模块只做装配: 建 FastAPI, 接中间件, 挂 router, 挂静态资源, 管进程生命周期。
路由住在 ``routers/``, 一个模块一种资源 (ADR-0048 决策 5) —— 原先它们全挤在这个
文件的一个函数里, 一千二百行, 改一条路由要从中间件读起, 而任何两条路由之间都没有
边界可言。

业务规则不在这一层: 路由做参数解析, 身份校验与响应折叠, 规则由应用层用例执行,
运行资源由 ``interfaces/runtime`` 装配与持有。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from forgecli.application.security.workspace_grants import GrantError
from forgecli.domain.workspace.project import WorkspaceError
from forgecli.interfaces.runtime.project_runtime import (
    ProjectRuntimeRegistry,
    build_project_service,
)
from forgecli.interfaces.web.routers import ROUTERS
from forgecli.interfaces.web.security import LocalControlPlaneGuard, SecurityState
from forgecli.shared import __version__

__all__ = ["create_app"]


def create_app(
    *,
    registry: ProjectRuntimeRegistry | None = None,
    boot_token: str | None = None,
    session_token: str | None = None,
    csrf_token: str | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    runtimes = registry or ProjectRuntimeRegistry(build_project_service())
    security = SecurityState(boot_token, session=session_token, csrf=csrf_token)
    # 服务停止信号：由启动器在等待连接收尾之前置位，长连接据此主动收尾。
    stopping = asyncio.Event()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            stopping.set()
            # close() 要 join Agent 线程并释放项目锁，放进线程里不阻塞事件循环。
            await asyncio.to_thread(runtimes.close)

    app = FastAPI(
        title="Forge Local Web API",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    # 路由经 app.state 拿这三样, 而不是闭包 —— 闭包是它们原先只能定义在这个函数里的
    # 唯一原因, 也就是这个文件长成一千二百行的原因。
    app.state.registry = runtimes
    app.state.security = security
    app.state.stopping = stopping
    app.add_middleware(LocalControlPlaneGuard, security=security)

    @app.exception_handler(WorkspaceError)
    async def workspace_error(_request: Request, exc: WorkspaceError) -> JSONResponse:
        return JSONResponse(
            {"detail": exc.message}, status_code=status.HTTP_422_UNPROCESSABLE_ENTITY
        )

    @app.exception_handler(GrantError)
    async def grant_error(_request: Request, exc: GrantError) -> JSONResponse:
        return JSONResponse(
            {"detail": exc.message}, status_code=status.HTTP_422_UNPROCESSABLE_ENTITY
        )

    for router in ROUTERS:
        app.include_router(router)

    assets = static_dir or Path(__file__).with_name("static")
    if not assets.is_dir():
        # 源码树里这个目录是构建产物, 不入库 (ADR-0025 决策 10: 最终用户装的 wheel
        # 里带着它, 所以只有从源码跑的人会撞上). StaticFiles 自己也会抛, 但它只说
        # "Directory does not exist", 不说该跑什么.
        raise RuntimeError(
            f"前端静态资源不存在: {assets}\n"
            "  从源码运行需要先构建一次: make web-install && make web-build"
        )
    # 挂在最后: 它吃掉 "/" 下所有剩余路径, 排在 router 之前会把 API 一起吞掉。
    app.mount("/", StaticFiles(directory=assets, html=True), name="web")
    return app
