"""shell_run: 唯一的 Shell 执行入口, 且**不是**安全决策者 (ADR-0013 §2).

这个工具刻意什么都不判断: 不解析命令, 不匹配规则, 不读 mode, 不调分类器. 它只做三件事
—— 声明"我可能干任何事", 把原始命令交出去让安全侧分析, 以及执行已经授权的那份计划.

两个看起来奇怪但必要的设计:

1. **prepare 声明的是完整能力上界, 不是最小集合.** 工具无法证明 `rm -rf x` 会不会联网,
   于是它把 spec 里的所有能力都声明上, 由 Shell 分析器收缩到实际发现的那些. 反过来做
   (先声明最小集, 分析器发现更多再补) 会撞上"改写只能收缩"的规则, 而放宽那条规则等于
   给能力升级开后门.
2. **target_resolution 是 UNKNOWN.** 工具层证明不了目标集合, 就老实说证明不了, 由安全侧
   的分析器展开并冻结, 再经 effective_plan 回写 (ADR-0004 §4).

**只接受原始命令串, 没有结构化 argv 入口** (ADR-0021). 执行永远是"把命令串交给非交互
shell", 一条路.

曾经有过第二条: `perform` 会优先用 `normalized_input["argv"]` 里"分析器冻结的结构化
argv". 那条分支从来没有生产方, 而且补一个也修不了它想修的问题 —— 把展开结果写进
normalized_input 之后仍旧交给 `sh -c`, shell 会把拼回去的串**重新解析一遍**, 字段值里的
`$`, 反引号, `;`, `&&`, glob 全部重新生效. 要让冻结成立, 执行路径必须自己按结构执行
(argv 直接 spawn, 管道由执行器建 fd, 重定向由执行器自己 open), 整条链路里不出现 shell;
而这个工具的入参就是一条 shell 命令串, 自己执行就得实现一遍 shell 语义.

所以两条路都留着只会让人以为冻结已经生效. 结论: 这个工具承认自己是不透明入口, 由恢复点
而不是 target_set_hash 兜住 glob 的二次展开.
"""

from __future__ import annotations

from types import MappingProxyType

from forgecli.application.tools.artifact_store import ArtifactStore
from forgecli.application.tools.builtin.base import emit_text, validate_arguments
from forgecli.application.tools.command_executor import (
    CommandExecutor,
    CommandOutcome,
    CommandRequest,
)
from forgecli.application.tools.resource_governor import ResourceGovernor
from forgecli.application.tools.tool import Tool, ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.errors import PreparationError, PreparationErrorCode
from forgecli.domain.tool.plan import (
    DeclarationConfidence,
    PlanEffects,
    ShellSubject,
    TargetResolution,
    ToolPlan,
    WorkspaceScope,
)
from forgecli.domain.tool.result import (
    ToolError,
    ToolMetrics,
    ToolResult,
    ToolResultStatus,
)
from forgecli.domain.tool.spec import (
    TargetDeclarationAbility,
    ToolAction,
    ToolSpec,
)
from forgecli.shared.cancellation import CancelToken

__all__ = ["ShellRunTool"]

# 方言取值与 domain.security.shell.ShellKind 的取值一致, 但这里刻意用字符串而不是
# import 那个枚举: 工具系统不能依赖安全模块 (ADR-0004 §13). 工具只负责如实转述
# "这是哪种方言"这个事实, 解析它是分析器的事.
_SHELL_KINDS = ("posix", "cmd", "powershell")

# 上界必须**真的**是上界: 分析器只能在这个集合里收缩, 集合外的事实即便被发现也无处
# 安放. WORKSPACE_READ 与 CREDENTIAL_ACCESS 早先不在这里, 于是 shell 读到的工作区文件
# 和脚本里的凭证访问都被交集悄悄抹掉了.
_CAPABILITIES = frozenset(
    {
        Capability.EXECUTE_SHELL,
        Capability.EXECUTE_SCRIPT,
        Capability.SPAWN_PROCESS,
        Capability.NETWORK_ACCESS,
        Capability.WORKSPACE_READ,
        Capability.WORKSPACE_WRITE,
        Capability.WORKSPACE_DELETE,
        Capability.PATH_MOVE,
        Capability.EXTERNAL_READ,
        Capability.EXTERNAL_WRITE,
        Capability.CREDENTIAL_ACCESS,
        Capability.EXTERNAL_IRREVERSIBLE_EFFECT,
    }
)

_SPEC = ToolSpec(
    name="shell_run",
    version="2",
    title="执行 Shell 命令",
    description="执行一条 Shell 命令. 复合命令, 管道和重定向都支持, 整条命令统一裁决.",
    input_schema={
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "shell_kind": {"type": "string", "enum": list(_SHELL_KINDS)},
            "timeout_seconds": {"type": "number", "minimum": 0.1, "maximum": 600},
        },
        "required": ["command"],
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "properties": {
            "stdout": {"type": "string"},
            "stderr": {"type": "string"},
            "exit_code": {"type": "integer"},
        },
    },
    declared_capabilities=_CAPABILITIES,
    # 工具层无法封闭目标集合: 这正是 opaque 的定义.
    target_declaration_ability=TargetDeclarationAbility.OPAQUE,
    default_timeout_seconds=120.0,
    action=ToolAction.EXECUTE,
)


class ShellRunTool(Tool):
    def __init__(
        self,
        executor: CommandExecutor,
        governor: ResourceGovernor,
        artifacts: ArtifactStore | None = None,
    ) -> None:
        self._executor = executor
        self._governor = governor
        self._artifacts = artifacts

    @property
    def spec(self) -> ToolSpec:
        return _SPEC

    def prepare(
        self, request: ToolInvocationRequest, context: ExecutionContext
    ) -> ToolPlan | PreparationError:
        invalid = validate_arguments(_SPEC, request.arguments)
        if invalid is not None:
            return invalid
        command = str(request.arguments.get("command", "")).strip()
        if not command:
            return PreparationError(
                code=PreparationErrorCode.INVALID_INPUT,
                message="command 不能为空",
                field_path="command",
            )
        shell_kind = self._shell_kind(context)
        requested_kind = request.arguments.get("shell_kind")
        if isinstance(requested_kind, str) and requested_kind != shell_kind:
            return PreparationError(
                code=PreparationErrorCode.INVALID_INPUT,
                message=(
                    f"shell_kind={requested_kind!r} 与当前执行环境 "
                    f"{shell_kind!r} 不一致"
                ),
                field_path="shell_kind",
            )
        return ToolPlan(
            plan_id=request.invocation_id,
            tool_name=_SPEC.name,
            spec_hash=_SPEC.spec_hash,
            normalized_input=MappingProxyType(
                {
                    "command": command,
                    "shell_kind": shell_kind,
                    "timeout_seconds": request.arguments.get("timeout_seconds"),
                }
            ),
            capabilities=_CAPABILITIES,
            effects=PlanEffects(child_process=True, dynamic_execution=True),
            target_resolution=TargetResolution.UNKNOWN,
            # 保守到最宽的一档: 分析器冻结目标之后才知道究竟碰了哪里.
            workspace_scope=WorkspaceScope.OUTSIDE,
            execution_context=context.to_ref(),
            declaration_confidence=DeclarationConfidence.OPAQUE,
            analysis_subject=ShellSubject(
                raw_command=command,
            ),
        )

    def perform(
        self,
        plan: ToolPlan,
        context: ExecutionContext,
        cancel: CancelToken | None = None,
    ) -> ToolResult:
        limits = self._governor.limits_for(_SPEC, timeout_override=_timeout_of(plan))
        argv = self._argv_of(plan, context)
        outcome = self._executor.run(
            CommandRequest(
                argv=argv,
                cwd=context.cwd,
                environment=context.environment,
                timeout_seconds=limits.timeout_seconds,
                max_output_bytes=limits.max_artifact_bytes,
                # 围栏边界随请求走 (ADR-0030 决策 1). 漏传的后果是执行器 fail closed,
                # 不是"没策略就不围".
                fence=context.fence,
            ),
            cancel,
        )
        text = _render(outcome)
        emitted = emit_text(
            text,
            invocation_id=plan.plan_id,
            limits=limits,
            artifacts=self._artifacts,
            artifact_name="shell_run",
        )
        metrics = ToolMetrics(
            duration_seconds=outcome.duration_seconds,
            exit_code=outcome.exit_code,
            bytes_out=emitted.bytes_out,
            child_process_count=outcome.child_process_count,
        )
        if _completed(outcome):
            # 非零退出**不是**工具错误, 是这条命令的结果.
            #
            # grep 无匹配退出 1, diff 有差异退出 1, test 判假退出 1 —— 都是正常结论.
            # 把它们标成 tool_error, 模型就得想办法绕过工具契约: 见过它给每条命令加
            # `|| echo "No matches found"`, 三轮模型调用花在跟工具斗, 后续命令还更难
            # 解析. 退出码由 _render 写进正文, 由模型自己判断这次算成功还是失败.
            return ToolResult(
                invocation_id=plan.plan_id,
                tool_name=_SPEC.name,
                status=ToolResultStatus.OK,
                content_parts=emitted.parts,
                artifacts=emitted.artifacts,
                metrics=metrics,
                provenance=emitted.provenance,
            )
        # 剩下的才是工具真的没跑成: 超时, 取消, 子进程起不来.
        return ToolResult(
            invocation_id=plan.plan_id,
            tool_name=_SPEC.name,
            status=_status_of(outcome.timed_out, outcome.cancelled),
            content_parts=emitted.parts,
            artifacts=emitted.artifacts,
            metrics=metrics,
            provenance=emitted.provenance,
            error=ToolError(
                code="shell_failed",
                message=outcome.failure or "命令没有执行完成",
            ),
        )

    def _argv_of(self, plan: ToolPlan, context: ExecutionContext) -> tuple[str, ...]:
        """用非交互 shell 跑原始命令. 只有这一条路.

        这里**不**再看 `normalized_input["argv"]`: 那条分支从来没有生产方, 而补一个
        生产方也修不了它想修的问题 —— 详见模块说明.
        """
        launch = context.profile.shell_launch
        return (launch.program, *launch.args, str(plan.normalized_input["command"]))

    @staticmethod
    def _shell_kind(context: ExecutionContext) -> str:
        # 分析方言必须由真实执行环境决定，不能由模型选择。
        return context.profile.shell_launch.dialect


def _timeout_of(plan: ToolPlan) -> float | None:
    raw = plan.normalized_input.get("timeout_seconds")
    return float(raw) if isinstance(raw, int | float) else None


def _completed(outcome: CommandOutcome) -> bool:
    """子进程是否跑到了自然结束. 与 CommandOutcome.succeeded 的区别只有一条: 不看退出码.

    succeeded 回答的是"这条命令干成了吗", 那是模型该判断的事; 这里回答的是"工具正常
    工作了吗", 那才是 ToolResultStatus 该表达的.
    """
    return (
        not outcome.timed_out
        and not outcome.cancelled
        and outcome.failure is None
        and outcome.exit_code is not None
    )


def _render(outcome: CommandOutcome) -> str:
    """给模型看的正文: 命令输出, 外加非零退出码.

    **退出码必须进正文.** 模型看不到 ToolMetrics —— 回填给它的只有 content_parts
    (ToolObservation.render 直接取 ToolResult.text). `grep` 无匹配时退出 1 且没有任何
    输出, 于是模型收到一个空字符串加一个 is_error 标记, 分不清"确实没找到"和"命令挂了".
    """
    body = outcome.stdout
    if outcome.stderr:
        body = f"{body}\n[stderr]\n{outcome.stderr}" if body else outcome.stderr
    if outcome.truncated:
        note = "[输出已达到执行器上限，stdout/stderr 与 artifact 都可能不完整]"
        body = f"{note}\n{body}" if body else note
    if outcome.exit_code in (0, None):
        return body
    note = f"[退出码 {outcome.exit_code}]"
    if body:
        return f"{body}\n{note}"
    # 空输出 + 非零退出是最容易被误读的一种: 说清它是"跑完了没输出", 不是"没跑成".
    return f"{note} 命令已执行完毕, 没有任何输出."


def _status_of(timed_out: bool, cancelled: bool) -> ToolResultStatus:
    if timed_out:
        return ToolResultStatus.TIMEOUT
    if cancelled:
        return ToolResultStatus.CANCELLED
    return ToolResultStatus.TOOL_ERROR
