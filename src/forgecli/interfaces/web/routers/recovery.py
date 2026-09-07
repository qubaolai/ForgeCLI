"""恢复层: 状态查询, 恢复点预览, 恢复与撤销。

恢复会改工作区, 所以三条写路由都要求这一轮已经结束 —— 在一轮跑到一半时把文件换回去,
那一轮接下来读到的就不是它自己刚写的东西。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from forgecli.interfaces.web.deps import active_runtime, idle_runtime
from forgecli.shared.serialization import to_jsonable

router = APIRouter(prefix="/api/v1")


class RestoreRequest(BaseModel):
    force_conflicts: bool = False


@router.get("/recovery")
async def recovery_status(request: Request) -> dict[str, object]:
    """恢复层状态与未收尾事务。"""
    return active_runtime(request).recovery_status()


@router.get("/checkpoints")
async def checkpoints(request: Request) -> dict[str, object]:
    runtime = active_runtime(request)
    return {"items": [to_jsonable(item) for item in runtime.list_checkpoints()]}


@router.get("/checkpoints/{checkpoint_id}/preview")
async def preview_checkpoint(checkpoint_id: str, request: Request) -> object:
    runtime = active_runtime(request)
    checkpoint = runtime.checkpoint(checkpoint_id)
    if checkpoint is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "恢复点不存在")
    return to_jsonable(
        runtime.tools.recovery.preview(checkpoint, runtime.tools.context_factory())
    )


@router.post("/checkpoints/{checkpoint_id}/restore")
async def restore_checkpoint(
    checkpoint_id: str, body: RestoreRequest, request: Request
) -> object:
    runtime = idle_runtime(request, "执行恢复")
    checkpoint = runtime.checkpoint(checkpoint_id)
    if checkpoint is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "恢复点不存在")
    return to_jsonable(
        runtime.tools.recovery.restore(
            checkpoint,
            runtime.tools.context_factory(),
            force_conflicts=body.force_conflicts,
        )
    )


@router.post("/undo")
async def undo_latest(request: Request) -> object:
    runtime = idle_runtime(request, "执行撤销")
    checkpoints = runtime.list_checkpoints()
    if not checkpoints:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "没有可撤销的操作")
    return to_jsonable(
        runtime.tools.recovery.restore(checkpoints[0], runtime.tools.context_factory())
    )
