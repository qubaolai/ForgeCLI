"""统一模型请求的值对象（ADR-0011 §3.3）。

所有模型调用共用同一请求结构: 非流式 / 流式 / 结构化都走它。换掉供应商适配器不改变
这个形状, 故属领域。

CancelToken 也在这里, 而且是本模块唯一可变的类型。它按身份比较 (eq=False), 表达的是
"这一次在途调用被叫停了"这件事本身 —— 在 DDD 术语里这是**实体**而非值对象, 可变是
它的本质不是妥协。ModelRequest 直接持有它, 分居两层就会让 domain 反向依赖 application。
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
class BudgetSnapshot:
    """本轮 / 本会话已用量与上限 (ADR-0011 §8 / §11.3).

    由 AgentTurnService 注入, gateway 只读比对, 不更新. 目前调用方还没开始填它, 所以
    实际总是 None —— 但 BudgetGuard 已经在消费这个字段, 它不是空位.
    """

    turn_tokens_used: int = 0
    turn_tokens_limit: int | None = None
    session_tokens_used: int = 0
    session_tokens_limit: int | None = None


@dataclass(frozen=True)
class CacheHint:
    """请求侧 prompt 缓存标注（§14）：标注可缓存前缀（system prompt / 稳定工具定义）。

    provider adapter 把它翻译成各供应商缓存机制（显式 breakpoint 或自动前缀缓存）；
    不支持 prompt 缓存的 provider 静默忽略，不报错。
    """

    cache_system_prompt: bool = False
    cache_tools: bool = False


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
    loop_step_id: str | None = None
    required_capabilities: tuple[str, ...] = ()
    min_context_window: int | None = None
    system_prompt: str | None = None
    tools: tuple[ToolSchema, ...] = ()
    timeout_seconds: float | None = None
    cancel_token: CancelToken | None = None
    budget_snapshot: BudgetSnapshot | None = None
    cache_hint: CacheHint | None = None
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
