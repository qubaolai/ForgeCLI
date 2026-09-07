"""受信项目的列举, 新增与激活。

本地服务同一时间只激活一个项目 (项目级 ``forge.lock``), 所以"激活"是一次写操作,
不是一个查询参数。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from forgecli.infrastructure.project import ProjectLockedError
from forgecli.interfaces.web.deps import project_payload, registry

router = APIRouter(prefix="/api/v1/projects")


class TrustProjectRequest(BaseModel):
    path: str = Field(min_length=1)


@router.get("")
async def list_projects(request: Request) -> dict[str, object]:
    projects = registry(request)
    active = projects.active
    return {
        "items": [
            project_payload(project) for project in projects.projects.list_trusted()
        ],
        "active_project_id": (None if active is None else active.project.project_id),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def trust_project(
    body: TrustProjectRequest, request: Request
) -> dict[str, object]:
    projects = registry(request).projects
    path = projects.normalize_workspace_dir(body.path, Path.cwd())
    existing = projects.find_trusted(path)
    return project_payload(existing or projects.trust(path))


@router.post("/{project_id}/activate")
async def activate_project(project_id: str, request: Request) -> dict[str, object]:
    try:
        runtime = registry(request).activate(project_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "项目不存在") from None
    except ProjectLockedError as exc:
        raise HTTPException(status.HTTP_423_LOCKED, exc.message) from exc
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return project_payload(runtime.project)
