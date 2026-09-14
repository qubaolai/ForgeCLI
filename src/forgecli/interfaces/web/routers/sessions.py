"""会话的列举, 新建, 恢复与正文读取, 以及会话模式.

模式放在这里而不是配置里: 它是**这个会话**的状态, 随会话切换而变, 而配置是跨会话的.

接口只认预设名 (ADR-0052): 隔离档与审批档两个轴仍是领域模型, 但界面不再按轴调, 于是
`{"sandbox": ...}` 这种请求也不再收 —— 一个没有任何页面会发的请求形状, 留着只会让读
路由的人以为前端还有那个入口.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from forgecli.domain.intents import (
    PRESET_NAMES,
    ModeUnavailableError,
    available_presets,
)
from forgecli.interfaces.web.deps import active_runtime, idle_runtime, registry
from forgecli.shared.errors import SessionStateError
from forgecli.shared.serialization import to_jsonable

router = APIRouter(prefix="/api/v1")


class ModeRequest(BaseModel):
    """`mode` 是预设名 (domain/intents 的 PRESET_NAMES 的键), 一次点击换整档."""

    mode: str


@router.get("/modes")
async def list_modes(request: Request) -> dict[str, object]:
    """本进程可选的预设, 按梯度排. 名字与说明来自 MODE_PRESETS, 前端不另写一份.

    每一项带上它对应的两个轴: 会话快照里的 `mode` 是 `{sandbox, approval}`, 前端靠这
    一对把当前模式对回预设名. Full Access 没在启动时放开时根本不在列表里, 与终端菜单
    同一个做法.

    从 registry 取而不是从当前 runtime: 放没放开是进程级事实, 页面在还没激活项目时
    就要画得出这个菜单.
    """
    allowed = registry(request).full_access_allowed
    return {
        "items": [
            {
                "value": preset.value,
                "label": preset.label,
                "hint": preset.hint,
                "sandbox": PRESET_NAMES[preset.value].sandbox.value,
                "approval": PRESET_NAMES[preset.value].approval.value,
            }
            for preset in available_presets(full_access_allowed=allowed)
        ]
    }


@router.get("/sessions")
async def list_sessions(request: Request) -> dict[str, object]:
    runtime = active_runtime(request)
    return {
        "current_session_id": runtime.session.current().session_id,
        "current_session": to_jsonable(runtime.session.current()),
        "items": [to_jsonable(item) for item in runtime.list_sessions()],
    }


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def new_session(request: Request) -> object:
    try:
        return to_jsonable(active_runtime(request).new_session())
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/sessions/{session_id}/resume")
async def resume_session(session_id: str, request: Request) -> object:
    try:
        return to_jsonable(active_runtime(request).resume(session_id))
    except SessionStateError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.message) from exc
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request) -> dict[str, object]:
    """删掉一个会话及其全部会话级数据。

    **立刻返回**: 这一步只保证会话已经从列表消失, 文件由后台接着清 (释放快照, 回收
    blob, rmtree 会话目录) —— 那部分耗时随项目大小走, 留在请求里迟早超时。

    删的是当前会话时, 返回体里的 ``current_session_id`` 是紧接着新开的那一个 ——
    客户端据此切过去, 而不是自己猜删完之后停在哪。
    """
    runtime = idle_runtime(request, "删除会话")
    try:
        report, current = runtime.delete_session(session_id)
    except SessionStateError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.message) from exc
    return {
        "deleted": report.session_id,
        "title": report.title,
        # 不报"清掉了几个恢复点": 那要等后台清完才知道。报一个还没发生的数字, 不如
        # 如实说清理还在跑。
        "cleanup": "scheduled",
        "current_session_id": current.session_id,
    }


@router.get("/sessions/{session_id}/transcript")
async def transcript(session_id: str, request: Request) -> dict[str, object]:
    try:
        items = active_runtime(request).transcript(session_id)
    except SessionStateError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.message) from exc
    return {"items": [to_jsonable(event) for event in items]}


@router.post("/mode")
async def set_mode(body: ModeRequest, request: Request) -> object:
    target = PRESET_NAMES.get(body.mode)
    if target is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"未知模式: {body.mode}"
        )
    runtime = active_runtime(request)
    try:
        return to_jsonable(runtime.set_mode(target))
    except ModeUnavailableError as exc:
        # 与 409 分开: 那一个是"现在不行, 等 turn 跑完", 这一个是"这个进程就没有这档".
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.message) from exc
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
