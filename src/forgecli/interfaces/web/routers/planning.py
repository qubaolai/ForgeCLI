"""计划与待办: 当前计划视图, Markdown 派生视图, 计划目录, 以及回合边界的计划评审。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel

from forgecli.application.planning.plan_review import PlanReviewChoice
from forgecli.interfaces.web.deps import active_runtime
from forgecli.shared.serialization import to_jsonable

router = APIRouter(prefix="/api/v1")


class PlanReviewRequest(BaseModel):
    decision: str
    note: str = ""


@router.get("/planning")
async def planning(request: Request) -> object:
    service = active_runtime(request).tools.planning
    payload = to_jsonable(service.load())
    assert isinstance(payload, dict)
    payload["markdown"] = service.read_plan()
    return payload


@router.get("/planning/markdown")
async def planning_markdown(request: Request) -> Response:
    """在浏览器中打开当前计划的本地 Markdown 派生视图。"""
    runtime = active_runtime(request)
    active = runtime.tools.planning.load().plan
    markdown = runtime.tools.planning.read_plan()
    if active is None or markdown is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "当前没有活动计划")
    filename = f"{active.plan_id}-r{active.revision}.md"
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.get("/plans")
async def plans(request: Request) -> object:
    """计划目录与活动指针。"""
    return to_jsonable(active_runtime(request).plan_index())


@router.post("/plans/{plan_id}/activate")
async def activate_plan(plan_id: str, request: Request) -> dict[str, bool]:
    if not active_runtime(request).activate_plan(plan_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "计划不存在")
    return {"activated": True}


@router.post("/plan-reviews/current/resolve")
async def resolve_plan_review(
    body: PlanReviewRequest, request: Request
) -> dict[str, bool]:
    try:
        choice = PlanReviewChoice(body.decision)
    except ValueError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "未知计划决议"
        ) from None
    if not active_runtime(request).resolve_plan_review(choice, body.note):
        raise HTTPException(status.HTTP_409_CONFLICT, "当前没有待评审计划")
    return {"resolved": True}
