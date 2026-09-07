"""运行时状态总览: 会话, 姿态, 当前模型与可操作目录。

只读聚合, 没有自己的状态 —— 它把别处已经成立的事实拼成一屏, 不参与任何裁决。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from forgecli.interfaces.web.deps import active_runtime

router = APIRouter(prefix="/api/v1")


@router.get("/status")
async def status_view(request: Request) -> dict[str, object]:
    return active_runtime(request).status()
