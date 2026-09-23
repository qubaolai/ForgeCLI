"""Tool 协议 (ADR-0054 决策 1; 对应 Claude Code 的 Tool.ts).

一个工具要回答的全部问题都在这里, 缺省值与 `buildTool()` 的 `TOOL_DEFAULTS` 同:
并发不安全, 非只读, 非破坏性, `check_permissions` 缺省 allow —— 安全来自每个真实
工具覆盖它, 不来自框架. 框架 (tools/execution.py) 只按固定顺序问, 并把回答变成
模型看得到的 tool_result.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType

from forgecli.common.cancellation import CancelToken
from forgecli.core.app_state import AppState
from forgecli.core.message import ChatMessage, ToolResultBlock
from forgecli.core.permissions import PermissionResult, allow
from forgecli.core.tool.file_state import FileStateCache
from forgecli.core.tool.observer import ToolRunObserver
from forgecli.core.tool.tool_call import ToolSchema
from forgecli.human_interaction.service import HumanPromptService

__all__ = [
    "DEFAULT_MAX_RESULT_SIZE_CHARS",
    "CanUseToolFn",
    "InterruptBehavior",
    "ProgressCallback",
    "PromptOptions",
    "SearchOrRead",
    "Tool",
    "ToolProgress",
    "ToolResult",
    "ToolUseContext",
    "ToolUseOptions",
    "ValidationResult",
]

# 结果落盘阈值的上限 (对应 constants/toolLimits.ts). 工具自己声明的更小就取更小的.
DEFAULT_MAX_RESULT_SIZE_CHARS = 50_000


class InterruptBehavior(Enum):
    CANCEL = "cancel"
    BLOCK = "block"


@dataclass(frozen=True)
class SearchOrRead:
    """这次调用是搜索 / 读取 / 列目录的哪一种, 界面据此折叠展示."""

    is_search: bool = False
    is_read: bool = False
    is_list: bool = False


@dataclass(frozen=True)
class ValidationResult:
    """`validate_input` 的返回. ok 为假时 message 直接回给模型."""

    ok: bool
    message: str = ""
    error_code: int = 0
    meta: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class ToolResult:
    """`call` 的返回. data 的形状由每个工具自己定, 由 map_tool_result_to_block 翻译."""

    data: object
    new_messages: tuple[ChatMessage, ...] = ()
    # 工具产出了需要人裁决的东西 (plan_write 交出一份计划), 本轮到此为止 (ADR-0023).
    await_user_decision: bool = False


@dataclass(frozen=True)
class ToolProgress:
    tool_use_id: str
    text: str


ProgressCallback = Callable[[ToolProgress], None]


@dataclass(frozen=True)
class PromptOptions:
    """`prompt()` 与 `description()` 拿到的环境: 当前工具池与权限上下文的读取器."""

    tools: tuple[Tool, ...] = ()
    get_app_state: Callable[[], AppState] | None = None


@dataclass
class ToolUseOptions:
    tools: tuple[Tool, ...]
    non_interactive: bool = False
    read_only: bool = False
    debug: bool = False
    main_loop_model: str = ""


@dataclass
class ToolUseContext:
    """执行上下文总线 (对应 ToolUseContext), 一次 turn 内共用."""

    options: ToolUseOptions
    abort: CancelToken
    read_file_state: FileStateCache
    messages: tuple[ChatMessage, ...]
    get_app_state: Callable[[], AppState]
    set_app_state: Callable[[AppState], None]
    cwd: str
    environment: Mapping[str, str]
    # 项目的根 (启动时的 cwd 与 /add-dir 之外的固定根). 额外目录在权限上下文里.
    workspace_roots: tuple[str, ...]
    session_id: str
    turn_id: str
    prompts: HumanPromptService
    observer: ToolRunObserver
    # 文件历史: Edit / Write 落盘前调它备份原件.
    track_file_edit: Callable[[str], None]
    # 超长结果落盘的目录.
    tool_results_dir: str
    # 项目内部路径 (计划文件目录等): 权限层视为可读可写.
    internal_paths: tuple[str, ...] = ()
    agent_id: str = ""
    tool_use_id: str = ""


# 权限裁决的入口: (tool, input, context, parent_message, tool_use_id) -> 决定.
CanUseToolFn = Callable[
    ["Tool", Mapping[str, object], ToolUseContext, ChatMessage | None, str],
    PermissionResult,
]


class Tool(ABC):
    """一个工具 (对应 Tool.ts 的 Tool 类型). 子类只须实现 call 与
    map_tool_result_to_block."""

    name: str = ""
    aliases: tuple[str, ...] = ()
    search_hint: str = ""
    input_schema: Mapping[str, object] = MappingProxyType({})
    output_schema: Mapping[str, object] | None = None
    strict: bool = False
    # 超过这个字符数就落盘 (ADR-0054 决策 5). 无穷大 = 永不落盘.
    max_result_size_chars: int = DEFAULT_MAX_RESULT_SIZE_CHARS

    # ---- 给模型的说明 ----

    def description(self, input: Mapping[str, object]) -> str:
        """给人看的一句 (界面). 缺省用 prompt 的第一行."""
        return self.prompt(PromptOptions()).strip().splitlines()[0]

    @abstractmethod
    def prompt(self, options: PromptOptions) -> str:
        """发给模型的工具说明 (tool schema 的 description)."""

    def to_model_schema(self, options: PromptOptions) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.prompt(options),
            parameters=MappingProxyType(dict(self.input_schema)),
        )

    # ---- 运行控制 ----

    def is_enabled(self) -> bool:
        return True

    def interrupt_behavior(self) -> InterruptBehavior:
        return InterruptBehavior.BLOCK

    def requires_user_interaction(self) -> bool:
        return False

    # ---- 安全属性, 缺省全部保守 ----

    def is_concurrency_safe(self, input: Mapping[str, object]) -> bool:
        return False

    def is_read_only(self, input: Mapping[str, object]) -> bool:
        return False

    def is_destructive(self, input: Mapping[str, object]) -> bool:
        return False

    def is_search_or_read_command(
        self, input: Mapping[str, object]
    ) -> SearchOrRead | None:
        return None

    def get_path(self, input: Mapping[str, object]) -> str | None:
        return None

    def check_permissions(
        self, input: Mapping[str, object], context: ToolUseContext
    ) -> PermissionResult:
        """缺省 allow, 与 buildTool 同. 通用逻辑在 security/permissions.py."""
        return allow(updated_input=input)

    # ---- 校验与规范化 ----

    def validate_input(
        self, input: Mapping[str, object], context: ToolUseContext
    ) -> ValidationResult:
        return ValidationResult(ok=True)

    def backfill_observable_input(self, input: dict[str, object]) -> None:
        """在入参副本上补派生字段 (路径展开一类), 给权限看. 必须幂等."""
        return None

    def inputs_equivalent(
        self, first: Mapping[str, object], second: Mapping[str, object]
    ) -> bool:
        return dict(first) == dict(second)

    # ---- 展示 ----

    def user_facing_name(self, input: Mapping[str, object]) -> str:
        return self.name

    def get_activity_description(self, input: Mapping[str, object]) -> str | None:
        return None

    def get_tool_use_summary(self, input: Mapping[str, object]) -> str | None:
        return None

    def to_auto_classifier_input(self, input: Mapping[str, object]) -> object:
        """这次调用给 auto 模式安全分类器看的紧凑形式 (ADR-0058 决策 3): Bash 是命令
        本身, Edit 是 `路径: 新内容`. 返回 "" 表示与安全无关, 不进分类器的记录 —— 缺省
        就是 "", 与 buildTool 同: 有安全含义的工具必须覆盖它."""
        return ""

    # ---- 执行 ----

    @abstractmethod
    def call(
        self,
        input: Mapping[str, object],
        context: ToolUseContext,
        can_use_tool: CanUseToolFn,
        parent_message: ChatMessage | None,
        on_progress: ProgressCallback | None = None,
    ) -> ToolResult: ...

    @abstractmethod
    def map_tool_result_to_block(
        self, output: object, tool_use_id: str
    ) -> ToolResultBlock: ...
