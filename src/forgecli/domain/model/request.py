"""统一模型请求的值对象（ADR-0011 §3.3）。

所有模型调用共用同一请求结构: 非流式 / 流式 / 结构化都走它。换掉供应商适配器不改变
这个形状, 故属领域。

CancelToken 已上移到 shared.cancellation: 它与"模型请求"无关, 工具执行 (ADR-0004 §10)
同样要用它, 留在这里会让工具系统反向 import domain.model。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from forgecli.domain.conversation.message import ChatMessage
from forgecli.domain.model.origin import RequestOrigin
from forgecli.domain.model.params import ModelParams
from forgecli.domain.model.selection import ModelSelection
from forgecli.domain.tool.tool_call import ToolSchema
from forgecli.shared.cancellation import CancelToken

# metadata 中禁止出现的凭证类键（子串匹配，大小写无关）。
# 不含裸 "token"，避免误伤 max_tokens 等安全摘要键。
_SECRETISH_KEY_PARTS = (
    "api_key",
    "apikey",
    "secret",
    "password",
    "credential",
    "authorization",
    "bearer",
)


def _assert_metadata_safe(metadata: Mapping[str, str]) -> None:
    for key in metadata:
        lowered = key.lower()
        if any(part in lowered for part in _SECRETISH_KEY_PARTS):
            raise ValueError(f"metadata 只能放安全摘要，不得含凭证类字段: {key!r}")


@dataclass(frozen=True)
class ModelRequest:
    """所有模型调用的统一请求（§3.3）。非流式 / 流式 / 结构化共用同一结构。"""

    request_id: str
    session_id: str
    turn_id: str
    origin: RequestOrigin
    model_selection: ModelSelection
    messages: tuple[ChatMessage, ...]
    params: ModelParams
    required_capabilities: tuple[str, ...] = ()
    min_context_window: int | None = None
    system_prompt: str | None = None
    tools: tuple[ToolSchema, ...] = ()
    timeout_seconds: float | None = None
    cancel_token: CancelToken | None = None
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not self.request_id.strip():
            raise ValueError("ModelRequest.request_id 不能为空")
        if not self.session_id.strip():
            raise ValueError("ModelRequest.session_id 不能为空")
        if not self.turn_id.strip():
            raise ValueError("ModelRequest.turn_id 不能为空")
        if self.min_context_window is not None and self.min_context_window <= 0:
            raise ValueError("min_context_window 必须为正整数")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须为正数")
        _assert_metadata_safe(self.metadata)


@dataclass(frozen=True)
class StructuredModelRequest:
    """结构化输出请求：在 ModelRequest 之上挂 schema（§3.7）。

    用于 title / summary / plan update / risk classification 等内部分类/生成任务；
    Agent 主循环 act 的工具调用走原生 tool calling，不经过 complete_structured。
    """

    model_request: ModelRequest
    schema: Mapping[str, object]
    schema_name: str
    strict: bool = True

    def __post_init__(self) -> None:
        if not self.schema_name.strip():
            raise ValueError("StructuredModelRequest.schema_name 不能为空")
