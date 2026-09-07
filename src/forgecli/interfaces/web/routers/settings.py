"""配置项的读, 改, 与恢复默认。

三条路由都直落 ``ConfigService`` —— 终端的 ``/config`` 走的是同一个用例 (ADR-0048
决策 6)。这一层只做参数解析与错误映射, 不判断"这个键能不能改", 那是声明说了算的。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from forgecli.interfaces.web.deps import active_runtime, idle_runtime

router = APIRouter(prefix="/api/v1/settings")


class ConfigUpdateRequest(BaseModel):
    value: str = Field(min_length=1)


@router.get("")
async def settings(request: Request) -> dict[str, object]:
    """配置视图。两个入口读的是同一个 ``value_views`` (ADR-0048 决策 6)。

    以前这里只回有效值, 于是网页显示不出"这个值是默认的还是我改过的", 也就没法
    提供"恢复默认"—— 而终端一直都有。界面各自推导来源的话, 迟早会推出两套。
    """
    return {
        "items": [
            {
                "key": view.key.name,
                # 中文名与说明住在 SCHEMA 里 (domain/config/config_keys). 页面不自己
                # 写一份: 同一个开关在 CLI 菜单和网页上叫不同名字, 两边都不会报错,
                # 只会让用户以为是两个开关.
                "label": view.key.title,
                "help": view.key.help,
                "level": view.key.level.name.lower(),
                "kind": view.key.kind.name.lower(),
                "value": view.value,
                "choices": list(view.key.choices),
                "default": view.key.default,
                "overridden": view.overridden,
                "effect": view.key.effect,
            }
            for view in active_runtime(request).config.value_views()
        ]
    }


@router.patch("/{key:path}")
async def update_setting(
    key: str, body: ConfigUpdateRequest, request: Request
) -> dict[str, str]:
    runtime = idle_runtime(request, "修改运行配置")
    try:
        runtime.config.set(key, body.value)
    except Exception as exc:  # 配置错误族都带可直接展示的信息
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return {"key": key, "value": runtime.config.display(key)}


@router.delete("/{key:path}")
async def reset_setting(key: str, request: Request) -> dict[str, str]:
    """删除用户覆盖, 让这一项重新继承 SCHEMA 默认值 (ADR-0048 决策 6)。

    与"把当前默认值写成一条覆盖"不是一回事: 后者在默认值改版之后会把用户钉死在
    旧值上, 而他从没有主动选过那个值。终端的"恢复默认"走的就是这个用例, 这里接的
    是同一个。
    """
    runtime = idle_runtime(request, "修改运行配置")
    try:
        runtime.config.unset(key)
    except Exception as exc:  # 未知键等配置错误族都带可直接展示的信息
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return {"key": key, "value": runtime.config.display(key)}
