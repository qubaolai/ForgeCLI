"""统一请求结构 ModelRequest / StructuredModelRequest（ADR-0011 §3.3 / §3.7）。

冻结日一次冻全 ModelRequest 字段位（含本阶段 no-op 的 budget_snapshot / cancel_token），
避免后续预算、取消、流式接入时再改动「已冻结」DTO。ADR §3.3 的字段顺序如下（dataclass
要求有默认值的字段在后，故源码顺序与 ADR 列举顺序不同，但*字段集合*与之一致）：

    request_id, session_id, turn_id, loop_step_id?, origin, model_selection,
    required_capabilities[], min_context_window?, messages[], system_prompt?,
    tools[], params, timeout_seconds?, budget_snapshot?, cancel_token?, metadata

关键边界：
    - ModelRequest *不带* stream 标志；是否流式由调用 complete / stream 决定（§3.3）。
    - metadata 只放安全摘要（mode、command 等），不得含 secret——构造期即校验。
    - budget_snapshot 由 AgentTurnService 注入，BudgetGuard 只读裁决（MVP no-op）。
    - cancel_token 承载取消信号，运行时接线在 07-07，今日只冻字段位。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from forgecli.domain.conversation.message import ChatMessage
from forgecli.domain.model.origin import RequestOrigin
from forgecli.domain.model.params import ModelParams
from forgecli.domain.model.selection import ModelSelection
from forgecli.domain.tool.tool_call import ToolSpec

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
class CacheHint:
    """请求侧 prompt 缓存标注（§14）：标注可缓存前缀（system prompt / 稳定工具定义）。

    provider adapter 把它翻译成各供应商缓存机制（显式 breakpoint 或自动前缀缓存）；
    不支持 prompt 缓存的 provider 静默忽略，不报错。
    """

    cache_system_prompt: bool = False
    cache_tools: bool = False


@dataclass(frozen=True)
class BudgetSnapshot:
    """预算快照占位（§8 / §11.3）。MVP 内 BudgetGuard no-op，字段位先冻。

    由 AgentTurnService 注入当前 turn/session 已用量与上限；gateway 只读比对，不更新。
    """

    turn_tokens_used: int = 0
    turn_tokens_limit: int | None = None
    session_tokens_used: int = 0
    session_tokens_limit: int | None = None


@dataclass(eq=False)
class CancelToken:
    """协作式取消信号占位（§8 / §9）。运行时接线在 07-07，今日只冻字段位与最小协议。"""

    _cancelled: bool = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled


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
    tools: tuple[ToolSpec, ...] = ()
    timeout_seconds: float | None = None
    budget_snapshot: BudgetSnapshot | None = None
    cancel_token: CancelToken | None = None
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
