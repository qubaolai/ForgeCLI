"""路由共用的取值与守卫 (ADR-0048 决策 5)。

只放"每条路由都要做一遍、而且做法必须一致"的三件事: 取当前运行时, 要求它空闲,
把领域对象折成响应体。**不是通用转发层** —— 这里不放任何业务规则, 业务规则在应用层。
"""

from __future__ import annotations

import asyncio

from fastapi import HTTPException, Request, status

from forgecli.interfaces.runtime.project_runtime import (
    ProjectRuntime,
    ProjectRuntimeRegistry,
)
from forgecli.shared.serialization import to_jsonable

__all__ = [
    "active_runtime",
    "idle_runtime",
    "project_payload",
    "registry",
    "stopping",
]


def registry(request: Request) -> ProjectRuntimeRegistry:
    state: ProjectRuntimeRegistry = request.app.state.registry
    return state


def stopping(request: Request) -> asyncio.Event:
    """服务停止信号。长连接据此主动收尾, 不拖住优雅退出。"""
    event: asyncio.Event = request.app.state.stopping
    return event


def active_runtime(request: Request) -> ProjectRuntime:
    runtime = registry(request).active
    if runtime is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "尚未激活项目")
    return runtime


def idle_runtime(request: Request, action: str) -> ProjectRuntime:
    """取当前运行时, 并要求它没有在跑 turn。

    ``action`` 是给人看的动词短语 ("切换模型"), 拼进那句 409。收一个参数而不是让每条
    路由自己写 if: 这一句原先在十几条路由里各抄一遍, 抄漏的那条不会报错 —— 它只会在
    一轮跑到一半时改掉那一轮正在用的配置。
    """
    runtime = active_runtime(request)
    if runtime.busy:
        raise HTTPException(status.HTTP_409_CONFLICT, f"turn 运行期间不能{action}")
    return runtime


def project_payload(project: object) -> dict[str, object]:
    payload = to_jsonable(project)
    assert isinstance(payload, dict)
    return payload
