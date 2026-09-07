"""学习到的授权规则: 列举, 撤销, 清理过期。

只回当前工作区的规则 —— workspace 范围的规则不能跨项目命中, 列出来也无从撤销。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from forgecli.interfaces.web.deps import active_runtime
from forgecli.shared.serialization import to_jsonable

router = APIRouter(prefix="/api/v1/rules")


@router.get("")
async def rules(request: Request) -> dict[str, object]:
    runtime = active_runtime(request)
    return {
        "items": [
            to_jsonable(rule)
            for rule in runtime.tools.learned.rules
            if rule.match.workspace_id == runtime.tools.workspace_id
        ]
    }


@router.delete("/{rule_id}")
async def revoke_rule(rule_id: str, request: Request) -> dict[str, bool]:
    if not active_runtime(request).tools.learned.revoke(rule_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "学习规则不存在")
    return {"revoked": True}


@router.post("/prune")
async def prune_rules(request: Request) -> dict[str, int]:
    return {"removed": active_runtime(request).tools.learned.prune()}
