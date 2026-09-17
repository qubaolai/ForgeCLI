"""权限规则 (ADR-0055 决策 2): 三张表按来源列出, 删一条.

内置 (policy) 来源只读; 用户与项目来源写回设置文件, 本会话来源随会话消失.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from forgecli.web.deps import active_runtime

router = APIRouter(prefix="/api/v1/permissions")


class RemoveRuleRequest(BaseModel):
    behavior: str = Field(min_length=1)
    source: str = Field(min_length=1)
    rule: str = Field(min_length=1)


@router.get("")
async def permissions(request: Request) -> dict[str, object]:
    runtime = active_runtime(request)
    permission = runtime.tools.permission
    return {
        "mode": permission.mode.value,
        "rules": runtime.permission_rules(),
        "additional_directories": [
            {"path": item.path, "source": item.source.value}
            for item in permission.additional_working_directories
        ],
    }


@router.post("/remove")
async def remove_rule(body: RemoveRuleRequest, request: Request) -> dict[str, bool]:
    runtime = active_runtime(request)
    if not runtime.remove_permission_rule(body.behavior, body.source, body.rule):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "规则不存在或不可删除")
    return {"removed": True}
