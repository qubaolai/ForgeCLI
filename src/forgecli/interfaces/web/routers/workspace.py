"""工作区根目录的列举, 授权与撤销。

一条路由改两处状态: 项目配置里的根列表, 和运行时的目录授权。两者必须一起动 ——
只加进配置而不授权, 下一轮工具仍然读不了它, 而界面上它已经在列表里了。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from forgecli.interfaces.runtime.project_runtime import ProjectRuntime
from forgecli.interfaces.web.deps import (
    active_runtime,
    idle_runtime,
    project_payload,
    registry,
)

router = APIRouter(prefix="/api/v1/workspace-roots")


class WorkspaceGrantRequest(BaseModel):
    path: str = Field(min_length=1)
    access: str = "read"


def _access(runtime: ProjectRuntime, root: str) -> str:
    if root == runtime.project.primary_workspace_root:
        return "write"
    grant = runtime.tools.grants.access_for(root)
    return "read" if grant is None else grant.value


@router.get("")
async def workspace_roots(request: Request) -> dict[str, object]:
    runtime = active_runtime(request)
    return {
        "primary": runtime.project.primary_workspace_root,
        "items": [
            {"path": root, "access": _access(runtime, root)}
            for root in runtime.project.workspace_roots
        ],
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_workspace_root(
    body: WorkspaceGrantRequest, request: Request
) -> dict[str, object]:
    runtime = idle_runtime(request, "修改工作区授权")
    if body.access not in {"read", "write"}:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "access 只能是 read/write"
        )
    projects = registry(request).projects
    path = projects.normalize_workspace_dir(
        body.path, Path(runtime.project.primary_workspace_root)
    )
    if str(path) != runtime.project.primary_workspace_root:
        runtime.grant_workspace(path, write=body.access == "write")
    updated = projects.add_workspace_dir(runtime.project, path)
    runtime.project = updated
    return project_payload(updated)


@router.delete("")
async def remove_workspace_root(path: str, request: Request) -> dict[str, object]:
    runtime = idle_runtime(request, "修改工作区授权")
    projects = registry(request).projects
    normalized = projects.normalize_workspace_dir(
        path, Path(runtime.project.primary_workspace_root)
    )
    updated = projects.remove_workspace_dir(runtime.project, normalized)
    runtime.revoke_workspace(normalized)
    runtime.project = updated
    return project_payload(updated)
