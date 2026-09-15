"""面向模型的工具词汇: ToolSchema 与 ToolCall (ADR-0011 §10).

这里的两个类型只服务于**模型协议**: ToolSchema 是发给供应商的工具声明, ToolCall 是
模型回传的调用意图. 它们不是工具系统的契约 —— 那套富结构在 spec.py / plan.py.

原名 ToolSpec 让位给 ADR-0004 的 ToolSpec: 同一个名字既表示"发给模型看的三个字段",
又表示"能力上界 + 副作用类别 + 冲突域 + 信任区"的完整规格, 会诱导调用方把前者当后者
用. ToolSchema 由 ToolSpec.to_model_schema() 单向派生.

原 mutating 字段一并退场: 能力门改看闭集能力词汇 (capability.MUTATING_CAPABILITIES),
一个 bool 表达不了"读凭证也是只读"这类差异.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

__all__ = ["ToolCall", "ToolSchema"]


@dataclass(frozen=True)
class ToolSchema:
    """发给模型的工具声明: 名字, 说明和入参 JSON schema.

    parameters 由 provider adapter 转成各供应商的 tool schema. description 只影响模型
    怎么用工具, **不参与安全裁决** (ADR-0004 §3).
    """

    name: str
    description: str
    parameters: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("ToolSchema.name 不能为空")


@dataclass(frozen=True)
class ToolCall:
    """模型返回的工具调用意图.

    tool_call_id 用于把 tool call 与后续 tool result 关联 (§10).
    arguments 为未执行的原始入参: 执行前必须经 schema 校验与安全裁决, 不可直接信任.
    """

    tool_call_id: str
    name: str
    arguments: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        if not self.tool_call_id.strip():
            raise ValueError("ToolCall.tool_call_id 不能为空")
        if not self.name.strip():
            raise ValueError("ToolCall.name 不能为空")
