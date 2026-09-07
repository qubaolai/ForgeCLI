"""会话的列举, 新建, 恢复与正文读取, 以及会话姿态 (sandbox / approval)。

姿态放在这里而不是配置里: 它是**这个会话**的状态, 随会话切换而变, 而配置是跨会话的。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from forgecli.domain.intents import ApprovalPolicy, SandboxLevel, SessionMode
from forgecli.interfaces.web.deps import active_runtime
from forgecli.shared.errors import SessionStateError
from forgecli.shared.serialization import to_jsonable

router = APIRouter(prefix="/api/v1")


class ModeRequest(BaseModel):
    """按轴设置姿态.

    两个轴独立: 只给 `sandbox` 就只动隔离档, 审批档保持不变, 反之亦然. `mode` 是整档
    预设的快捷方式 (也接受 `sandbox/approval` 形式的整串), 与另外两个字段互斥使用.
    """

    mode: str = ""
    sandbox: str = ""
    approval: str = ""


def _resolve_mode(body: ModeRequest, current: SessionMode) -> SessionMode:
    """把一次请求折成目标姿态.

    没给的那个轴保持不变 —— 这就是"正交"在接口上的样子: 调隔离档不该顺手把审批档也
    改了. 早先两个轴压在一个四档枚举里, 想只动其中一件事是表达不出来的.
    """
    if body.mode:
        try:
            return SessionMode.from_value(body.mode)
        except ValueError:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"未知模式: {body.mode}"
            ) from None
    try:
        sandbox = SandboxLevel(body.sandbox) if body.sandbox else current.sandbox
        approval = ApprovalPolicy(body.approval) if body.approval else current.approval
    except ValueError as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"未知取值: {error}"
        ) from None
    return SessionMode(sandbox=sandbox, approval=approval)


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


@router.get("/sessions/{session_id}/transcript")
async def transcript(session_id: str, request: Request) -> dict[str, object]:
    try:
        items = active_runtime(request).transcript(session_id)
    except SessionStateError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, exc.message) from exc
    return {"items": [to_jsonable(event) for event in items]}


@router.post("/mode")
async def set_mode(body: ModeRequest, request: Request) -> object:
    try:
        runtime = active_runtime(request)
        target = _resolve_mode(body, runtime.session.current().mode)
        return to_jsonable(runtime.set_mode(target))
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
