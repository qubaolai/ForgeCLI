"""权限模块的类型 (ADR-0055 决策 2 / 4; 对应 Claude Code 的 types/permissions.ts).

只有类型与常量, 没有任何 IO. 单独放在 core 里是为了断 import 环: `Tool` 协议要引用
`PermissionResult`, 权限逻辑 (security/) 又要引用 `Tool`; 两边都只 import 这里.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum

from forgecli.common.errors import ForgeError

__all__ = [
    "MODE_OPTIONS",
    "AdditionalWorkingDirectory",
    "DenialTrackingState",
    "ModeOption",
    "ModeUnavailableError",
    "DecisionReasonType",
    "PermissionBehavior",
    "PermissionDecisionReason",
    "PermissionMode",
    "PermissionResult",
    "PermissionRule",
    "PermissionRuleSource",
    "PermissionRuleValue",
    "PermissionUpdate",
    "PermissionUpdateDestination",
    "PermissionUpdateType",
    "ResultBehavior",
    "ToolPermissionContext",
    "allow",
    "ask",
    "available_modes",
    "classifier_reason",
    "deny",
    "empty_tool_permission_context",
    "mode_reason",
    "other_reason",
    "parse_permission_mode",
    "passthrough",
    "rule_reason",
    "rule_text",
    "safety_check_reason",
    "sandbox_override_reason",
    "subcommand_results_reason",
    "working_dir_reason",
]


class PermissionMode(Enum):
    """五档模式 (ADR-0055 决策 1). 取值落盘进 state.json 与设置文件, 不可改."""

    # 规则说了算, 其余问.
    DEFAULT = "default"
    # 裁决与 default 同; 系统提示词要求只出方案, 出 plan 时回 pre_plan_mode.
    PLAN = "plan"
    # 工作目录内的写自动放行.
    ACCEPT_EDITS = "accept_edits"
    # 除 deny 规则, 内容级 ask 规则与安全检查外全部放行.
    BYPASS_PERMISSIONS = "bypass_permissions"
    # ask 一律成 deny: 无人值守的入口用.
    DONT_ASK = "dont_ask"
    # ask 交给安全分类器 (ADR-0058): 分类器说不拦就执行, 说拦就 deny, 拦得太多退回问人.
    AUTO = "auto"


# 旧写法 -> 新模式 (ADR-0055 影响里的对照表). 旧 state.json 与旧 forge.json 里可能
# 还留着. 单独一个 "auto" 不在表里: 它现在是 PermissionMode.AUTO 自己的取值 (ADR-0058),
# 旧预设 "auto" (围栏内自动) 的两轴写法 workspace_write/auto 仍映射到 accept_edits.
_LEGACY_MODES: dict[str, PermissionMode] = {
    "manual": PermissionMode.DEFAULT,
    "full_access": PermissionMode.BYPASS_PERMISSIONS,
    "acceptEdits": PermissionMode.ACCEPT_EDITS,
    "bypassPermissions": PermissionMode.BYPASS_PERMISSIONS,
    "dontAsk": PermissionMode.DONT_ASK,
    "workspace_write/manual": PermissionMode.DEFAULT,
    "read_only/always": PermissionMode.PLAN,
    "workspace_write/always": PermissionMode.ACCEPT_EDITS,
    "workspace_write/auto": PermissionMode.ACCEPT_EDITS,
    "full_access/never": PermissionMode.BYPASS_PERMISSIONS,
}


def parse_permission_mode(raw: str) -> PermissionMode:
    """认新名字也认旧写法. 认不出抛 ValueError."""
    for mode in PermissionMode:
        if mode.value == raw:
            return mode
    legacy = _LEGACY_MODES.get(raw)
    if legacy is not None:
        return legacy
    raise ValueError(f"认不出的模式: {raw!r}")


class ModeUnavailableError(ForgeError):
    """要切的模式没有放开 (bypass_permissions 没在启动时放开, auto 被设置锁了).
    message 可直接展示."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class ModeOption:
    """一档模式在界面上的样子: 值, 名字, 一句说明. 终端与 Web 照着同一张表渲染."""

    value: str
    label: str
    hint: str


# 菜单里的次序就是 Tab 循环的次序 (security/next_mode.py; 与 Claude Code 的
# getNextPermissionMode 同: plan 之后先 bypass 再 auto). dont_ask 不进菜单.
MODE_OPTIONS: tuple[ModeOption, ...] = (
    ModeOption(
        PermissionMode.DEFAULT.value,
        "Default",
        "读取直接执行, 编辑与命令按规则裁决, 其余问你",
    ),
    ModeOption(
        PermissionMode.ACCEPT_EDITS.value,
        "Accept Edits",
        "自动接受工作目录内的文件编辑",
    ),
    ModeOption(PermissionMode.PLAN.value, "Plan", "只出方案, 先评审再动手"),
    ModeOption(
        PermissionMode.BYPASS_PERMISSIONS.value,
        "Bypass Permissions",
        "除 deny 规则与安全检查外不再询问",
    ),
    ModeOption(
        PermissionMode.AUTO.value,
        "Auto",
        "需要确认的操作交给安全分类器: 只拦有害与越界的, 其余直接执行",
    ),
)


def available_modes(
    *, bypass_available: bool, auto_available: bool = False
) -> tuple[ModeOption, ...]:
    """本进程能进的模式. 没放开的档不出现, 而不是出现了再拒绝."""
    hidden: set[str] = set()
    if not bypass_available:
        hidden.add(PermissionMode.BYPASS_PERMISSIONS.value)
    if not auto_available:
        hidden.add(PermissionMode.AUTO.value)
    return tuple(option for option in MODE_OPTIONS if option.value not in hidden)


class PermissionBehavior(Enum):
    """一条规则的行为."""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class PermissionRuleSource(Enum):
    """规则从哪来 (ADR-0055 决策 2)."""

    # ~/.forge/config.json 的 permissions 段, 跨项目.
    USER_SETTINGS = "user_settings"
    # ~/.forge/projects/<id>/forge.json 的 permissions 段.
    PROJECT_SETTINGS = "project_settings"
    # 代码里内置, 不可被其他来源覆盖 (凭证目录一类).
    POLICY_SETTINGS = "policy_settings"
    # --allowed-tools / --disallowed-tools.
    CLI_ARG = "cli_arg"
    # 审批时选"本会话", 只在内存.
    SESSION = "session"


@dataclass(frozen=True)
class PermissionRuleValue:
    """一条规则的内容: 工具名, 以及可选的内容 (`Bash(git commit:*)` 里括号内那段)."""

    tool_name: str
    rule_content: str | None = None


@dataclass(frozen=True)
class PermissionRule:
    source: PermissionRuleSource
    rule_behavior: PermissionBehavior
    rule_value: PermissionRuleValue


@dataclass(frozen=True)
class AdditionalWorkingDirectory:
    path: str
    source: PermissionRuleSource


class PermissionUpdateDestination(Enum):
    USER_SETTINGS = "user_settings"
    PROJECT_SETTINGS = "project_settings"
    SESSION = "session"


class PermissionUpdateType(Enum):
    ADD_RULES = "add_rules"
    REPLACE_RULES = "replace_rules"
    REMOVE_RULES = "remove_rules"
    SET_MODE = "set_mode"
    ADD_DIRECTORIES = "add_directories"
    REMOVE_DIRECTORIES = "remove_directories"


@dataclass(frozen=True)
class PermissionUpdate:
    """一次对权限上下文的改动 (对应 PermissionUpdate). 审批建议与 /permissions 都用
    它."""

    type: PermissionUpdateType
    destination: PermissionUpdateDestination
    rules: tuple[PermissionRuleValue, ...] = ()
    behavior: PermissionBehavior | None = None
    mode: PermissionMode | None = None
    directories: tuple[str, ...] = ()


class DecisionReasonType(Enum):
    RULE = "rule"
    MODE = "mode"
    SUBCOMMAND_RESULTS = "subcommand_results"
    SANDBOX_OVERRIDE = "sandbox_override"
    WORKING_DIR = "working_dir"
    SAFETY_CHECK = "safety_check"
    # auto 模式下分类器的裁决 (ADR-0058).
    CLASSIFIER = "classifier"
    OTHER = "other"


@dataclass(frozen=True)
class PermissionDecisionReason:
    """为什么这么裁 (对应 PermissionDecisionReason). 按 type 读对应的字段."""

    type: DecisionReasonType
    rule: PermissionRule | None = None
    mode: PermissionMode | None = None
    reason: str = ""
    subcommand_results: tuple[tuple[str, PermissionResult], ...] = ()
    # 哪个分类器 (CLASSIFIER 才有).
    classifier: str = ""
    # SAFETY_CHECK 才有: 为真时 auto 模式让分类器看着上下文裁, 而不是一定问人. 敏感文件
    # 路径 (.git, .forge, shell 配置) 为真; 可疑的 Windows 路径与 UNC 路径为假.
    classifier_approvable: bool = False

    def describe(self) -> str:
        """给事件与日志看的一句话."""
        if self.type is DecisionReasonType.RULE and self.rule is not None:
            return f"rule {rule_text(self.rule)} ({self.rule.source.value})"
        if self.type is DecisionReasonType.MODE and self.mode is not None:
            return f"mode {self.mode.value}"
        if self.type is DecisionReasonType.CLASSIFIER:
            return f"classifier {self.classifier}: {self.reason}"
        if self.type is DecisionReasonType.SUBCOMMAND_RESULTS:
            parts: list[str] = []
            for command, result in self.subcommand_results:
                parts.append(f"{command} -> {result.behavior.value}")
            return "; ".join(parts)
        return f"{self.type.value}: {self.reason}"


def rule_text(rule: PermissionRule) -> str:
    """`Bash(git commit:*)` 这种写法. 解析在 security/permission_rule_parser.py."""
    value = rule.rule_value
    if value.rule_content is None:
        return value.tool_name
    return f"{value.tool_name}({value.rule_content})"


def rule_reason(rule: PermissionRule) -> PermissionDecisionReason:
    return PermissionDecisionReason(type=DecisionReasonType.RULE, rule=rule)


def mode_reason(mode: PermissionMode) -> PermissionDecisionReason:
    return PermissionDecisionReason(type=DecisionReasonType.MODE, mode=mode)


def other_reason(reason: str) -> PermissionDecisionReason:
    return PermissionDecisionReason(type=DecisionReasonType.OTHER, reason=reason)


def safety_check_reason(
    reason: str, *, classifier_approvable: bool = False
) -> PermissionDecisionReason:
    return PermissionDecisionReason(
        type=DecisionReasonType.SAFETY_CHECK,
        reason=reason,
        classifier_approvable=classifier_approvable,
    )


def classifier_reason(classifier: str, reason: str) -> PermissionDecisionReason:
    return PermissionDecisionReason(
        type=DecisionReasonType.CLASSIFIER, classifier=classifier, reason=reason
    )


def working_dir_reason(reason: str) -> PermissionDecisionReason:
    return PermissionDecisionReason(type=DecisionReasonType.WORKING_DIR, reason=reason)


def sandbox_override_reason(reason: str) -> PermissionDecisionReason:
    return PermissionDecisionReason(
        type=DecisionReasonType.SANDBOX_OVERRIDE, reason=reason
    )


def subcommand_results_reason(
    results: tuple[tuple[str, PermissionResult], ...],
) -> PermissionDecisionReason:
    return PermissionDecisionReason(
        type=DecisionReasonType.SUBCOMMAND_RESULTS, subcommand_results=results
    )


class ResultBehavior(Enum):
    """`check_permissions` 的四种回答. passthrough 只在工具与框架之间出现, 不进决定."""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    PASSTHROUGH = "passthrough"


@dataclass(frozen=True)
class PermissionResult:
    """一次裁决 (对应 PermissionResult / PermissionDecision).

    behavior 是 allow / deny / ask 时它就是最终决定; passthrough 表示工具没表态, 框架
    会把它变成 ask.
    """

    behavior: ResultBehavior
    message: str = ""
    # 权限层或人改过的入参; None 表示没改.
    updated_input: Mapping[str, object] | None = None
    decision_reason: PermissionDecisionReason | None = None
    suggestions: tuple[PermissionUpdate, ...] = ()
    blocked_path: str = ""
    user_modified: bool = False
    # 用户批准时顺带说的话, 追加在 tool_result 后面.
    accept_feedback: str = ""

    @property
    def is_allow(self) -> bool:
        return self.behavior is ResultBehavior.ALLOW

    @property
    def is_deny(self) -> bool:
        return self.behavior is ResultBehavior.DENY

    @property
    def is_ask(self) -> bool:
        return self.behavior is ResultBehavior.ASK

    @property
    def is_passthrough(self) -> bool:
        return self.behavior is ResultBehavior.PASSTHROUGH


def allow(
    updated_input: Mapping[str, object] | None = None,
    reason: PermissionDecisionReason | None = None,
    *,
    user_modified: bool = False,
    accept_feedback: str = "",
) -> PermissionResult:
    return PermissionResult(
        behavior=ResultBehavior.ALLOW,
        updated_input=updated_input,
        decision_reason=reason,
        user_modified=user_modified,
        accept_feedback=accept_feedback,
    )


def deny(message: str, reason: PermissionDecisionReason) -> PermissionResult:
    return PermissionResult(
        behavior=ResultBehavior.DENY, message=message, decision_reason=reason
    )


def ask(
    message: str,
    reason: PermissionDecisionReason | None = None,
    *,
    suggestions: tuple[PermissionUpdate, ...] = (),
    blocked_path: str = "",
    updated_input: Mapping[str, object] | None = None,
) -> PermissionResult:
    return PermissionResult(
        behavior=ResultBehavior.ASK,
        message=message,
        decision_reason=reason,
        suggestions=suggestions,
        blocked_path=blocked_path,
        updated_input=updated_input,
    )


def passthrough(
    message: str,
    reason: PermissionDecisionReason | None = None,
    *,
    suggestions: tuple[PermissionUpdate, ...] = (),
) -> PermissionResult:
    return PermissionResult(
        behavior=ResultBehavior.PASSTHROUGH,
        message=message,
        decision_reason=reason,
        suggestions=suggestions,
    )


@dataclass(frozen=True)
class ToolPermissionContext:
    """一次会话的权限上下文 (对应 ToolPermissionContext). 三张规则表存规则原文,
    按来源分组."""

    mode: PermissionMode = PermissionMode.DEFAULT
    additional_working_directories: tuple[AdditionalWorkingDirectory, ...] = ()
    always_allow_rules: Mapping[PermissionRuleSource, tuple[str, ...]] = field(
        default_factory=dict
    )
    always_deny_rules: Mapping[PermissionRuleSource, tuple[str, ...]] = field(
        default_factory=dict
    )
    always_ask_rules: Mapping[PermissionRuleSource, tuple[str, ...]] = field(
        default_factory=dict
    )
    is_bypass_permissions_mode_available: bool = False
    # auto 要有分类器可用且设置里没锁 (ADR-0058 决策 1).
    is_auto_mode_available: bool = False
    # 没有人机界面时 ask 直接成 deny, 不摆卡片.
    should_avoid_permission_prompts: bool = False
    # 进 plan 时记下来时的模式, 出 plan 恢复.
    pre_plan_mode: PermissionMode | None = None
    # 进 auto 时剥掉的那些会绕过分类器的 allow 规则 (Bash(python:*) 一类), 按来源存,
    # 出 auto 放回去. None = 不在 auto 里 (对应 strippedDangerousRules).
    stripped_dangerous_rules: Mapping[PermissionRuleSource, tuple[str, ...]] | None = (
        None
    )


@dataclass(frozen=True)
class DenialTrackingState:
    """auto 模式下分类器拦了多少次 (对应 denialTracking.ts). 连续太多次或累计太多次
    就退回问人 (ADR-0058 决策 4); 阈值与计数函数在 security/denial_tracking.py."""

    consecutive_denials: int = 0
    total_denials: int = 0


def empty_tool_permission_context() -> ToolPermissionContext:
    return ToolPermissionContext()
