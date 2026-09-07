"""待答提示的列举与作答。

审批与 ``ask_user`` 走同一条队列 (ADR-0043 决策 3), 所以这里也不按工具名分派 ——
"这条提示是什么"由 ``HumanPrompt.kind`` 说, 而作答是否合法由提示自己校验。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from forgecli.interfaces.web.deps import active_runtime

router = APIRouter(prefix="/api/v1/prompts")


class PromptResolveRequest(BaseModel):
    """一次作答. 审批只用 choice, 提问两者都可能有 (ADR-0043 决策 3)."""

    choice: str = ""
    text: str = ""
    selected_values: list[str] = Field(default_factory=list)
    skipped: bool = False


@router.get("")
async def pending_prompts(request: Request) -> dict[str, object]:
    return {"items": list(active_runtime(request).prompts.list_pending())}


@router.post("/{prompt_id}/resolve")
async def resolve_prompt(
    prompt_id: str, body: PromptResolveRequest, request: Request
) -> dict[str, bool]:
    resolved = active_runtime(request).resolve_prompt(
        prompt_id,
        body.choice,
        body.text,
        selected_values=tuple(body.selected_values),
        skipped=body.skipped,
    )
    if not resolved:
        raise HTTPException(status.HTTP_409_CONFLICT, "该提示不在等待中或作答无效")
    return {"resolved": True}
