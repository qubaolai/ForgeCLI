"""值对象的安全 JSON 转换。

住在 shared 而不是 interfaces: 处理过程要落盘 (infrastructure/session/jsonl_run_store),
而落下去的形状必须与发给页面的那一份**逐字节相同** —— 前端因此不需要为"历史的"和
"进行中的"各写一套渲染。两边各写一个转换器就是两份会漂的真相。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel

from forgecli.domain.model.thinking import ThinkingEffortName


def to_jsonable(value: object) -> Any:
    """只展开值对象、枚举和基础容器，不调用任意对象的 ``__dict__``。"""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Enum):
        return value.value
    # ThinkingEffortName 是一个带校验的标量值对象。它在配置文件和 Web DTO 中
    # 的公开形状都是强度名字符串，而不是内部 dataclass 的 ``{"value": ...}``。
    # 这个分支必须放在通用 dataclass 展开之前，否则前端输入框会收到对象并显示
    # 为 ``[object Object]``。
    if isinstance(value, ThinkingEffortName):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: to_jsonable(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, BaseModel):
        # 与 dataclass 同类: 字段是**声明**出来的, 展开它不等于展开 __dict__.
        #
        # 逐字段递归而不是 model_dump(): dump 会按 Pydantic 自己的规则序列化整棵树,
        # 于是嵌在里面的 dataclass 和枚举就绕过了上面那几条白名单. 这个函数的价值正是
        # "认不出的类型直接抛", 交给 dump 就没有了.
        return {
            name: to_jsonable(getattr(value, name)) for name in type(value).model_fields
        }
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list | set | frozenset):
        return [to_jsonable(item) for item in value]
    raise TypeError(f"不支持的 Web DTO 类型: {type(value).__name__}")
