"""按资源组织的路由

"""

from __future__ import annotations

from fastapi import APIRouter

from forgecli.web.routers import (
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
