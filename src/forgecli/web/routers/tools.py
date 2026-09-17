"""工具清单。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from forgecli.core.tool.tool import PromptOptions
from forgecli.web.deps import active_runtime

router = APIRouter(prefix="/api/v1")


@router.get("/tools")
async def tools(request: Request) -> dict[str, object]:
    """两份清单, 因为界面上有两个问题.

    `items` 是**当前权限上下文下模型可见的**工具 (deny 规则摘掉的不在), 管理面板照这个
    语义展示. `all` 是全部内置工具, 与规则无关: 运行过程视图要把历史事件里的工具名翻成
    说明, 而那些调用可能发生在规则改变之前.
    """
    runtime = active_runtime(request)
    runner = runtime.tools.runner
    pool = runner.pool()
    options = PromptOptions(tools=pool, get_app_state=runner.get_app_state)
    return {
        "items": [
            {
                "name": tool.name,
                "description": tool.prompt(options),
                "parameters": dict(tool.input_schema),
            }
            for tool in pool
        ],
        "all": [
            {
                "name": tool.name,
                "title": tool.prompt(options).strip().splitlines()[0][:80],
                "read_only": tool.is_read_only({}),
            }
            for tool in runner.base_tools
        ],
    }
