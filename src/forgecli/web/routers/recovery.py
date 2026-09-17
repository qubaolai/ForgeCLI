"""文件历史 (ADR-0054 决策 5): 列快照, 回到某条消息之前.

回退会改工作区, 所以写路由要求这一轮已经结束 —— 在一轮跑到一半时把文件换回去, 那一轮
接下来读到的就不是它自己刚写的东西.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from forgecli.web.deps import active_runtime, idle_runtime

router = APIRouter(prefix="/api/v1")


@router.get("/rewind")
async def rewind_points(request: Request) -> dict[str, object]:
    runtime = active_runtime(request)
    return {
        "items": [
            {
                "message_id": item.message_id,
                "created_at": item.created_at,
                "files": sorted(item.backups),
            }
            for item in runtime.rewind_points()
        ]
    }


@router.post("/rewind/{message_id}")
async def rewind(message_id: str, request: Request) -> dict[str, object]:
    runtime = idle_runtime(request, "回退文件")
    if not runtime.tools.file_history.can_restore(message_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "没有这条消息的快照")
    outcome = runtime.rewind(message_id)
    return {
        "restored": list(outcome.restored),
        "removed": list(outcome.removed),
        "failed": list(outcome.failed),
    }
