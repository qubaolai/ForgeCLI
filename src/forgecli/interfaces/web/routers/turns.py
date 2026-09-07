"""一轮对话的起停与在途状态。

``run_id`` 与 ``(session_id, turn_id)`` 一起报 (ADR-0048 决策 2): 前者标识这次后台
执行, 后者标识会话里的第几轮, 两者不能互相代用。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from forgecli.domain.intents import InputOrigin
from forgecli.interfaces.web.deps import active_runtime
from forgecli.shared.serialization import to_jsonable

router = APIRouter(prefix="/api/v1/turns")


class MessageRequest(BaseModel):
    text: str = Field(min_length=1)


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def start_turn(body: MessageRequest, request: Request) -> object:
    try:
        return to_jsonable(
            active_runtime(request).start_turn(body.text, origin=InputOrigin.WEB_USER)
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("/current")
async def current_turn(request: Request) -> object:
    runtime = active_runtime(request)
    payload = to_jsonable(runtime.current_run())
    identity = runtime.current_turn_identity()
    if isinstance(payload, dict) and identity is not None:
        payload["session_id"] = identity.session_id
        payload["turn_id"] = identity.turn_id
    return payload


@router.post("/current/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_turn(request: Request) -> dict[str, bool]:
    return {"cancel_requested": active_runtime(request).cancel()}
