"""工具清单。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from forgecli.interfaces.web.deps import active_runtime
from forgecli.shared.serialization import to_jsonable

router = APIRouter(prefix="/api/v1")


@router.get("/tools")
async def tools(request: Request) -> dict[str, object]:
    """两份清单, 因为界面上有两个问题.

    `items` 是**当前模式下模型可见的**工具, 管理面板照这个语义展示 —— 换模式这份
    就该跟着变.

    `all` 是全部已注册工具的展示名与动作, 与模式无关: 运行过程视图要把历史事件里的
    工具名翻成中文, 而那些调用可能发生在换模式之前, 按当前模式过滤的那份里没有它们.
    前端曾经为此自带一张手抄表, 于是 `artifact_read` 在后端叫"读回已归档的输出",
    在界面上叫"读取产物".
    """
    runtime = active_runtime(request)
    catalog = runtime.tools.dispatcher.catalog_for(runtime.session.current().mode)
    return {
        "items": [to_jsonable(item) for item in catalog.entries],
        "all": [
            {
                "name": spec.name,
                "title": spec.title,
                # 归类按能力, 不按另立的分类字段: 能力是安全层已经在消费的真相
                # (catalog_predicates 就按它过滤目录), 而原先那个 action 只有
                # 展示这一个消费方 (ADR-0042 决策 6).
                "capabilities": sorted(
                    item.value for item in spec.declared_capabilities
                ),
            }
            for spec in runtime.tools.registry.describe_all()
        ],
    }
