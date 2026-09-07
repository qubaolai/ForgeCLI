"""按资源组织的路由 (ADR-0048 决策 5)。

一个模块一种资源: 参数解析, 身份校验, 错误映射与响应折叠都在自己这一份里, 业务规则
在应用层。挂载顺序无关 —— 路径彼此不重叠, 所以这里按名字排, 不按依赖排。
"""

from __future__ import annotations

from fastapi import APIRouter

from forgecli.interfaces.web.routers import (
    handshake,
    human_interaction,
    models,
    planning,
    projects,
    recovery,
    rules,
    run_events,
    sessions,
    settings,
    status,
    tools,
    turns,
    workspace,
)

__all__ = ["ROUTERS"]

ROUTERS: tuple[APIRouter, ...] = (
    handshake.router,
    projects.router,
    sessions.router,
    turns.router,
    run_events.router,
    human_interaction.router,
    planning.router,
    settings.router,
    models.router,
    tools.router,
    workspace.router,
    rules.router,
    recovery.router,
    status.router,
)
