"""EXECUTE_SHELL 能力分析器 (ADR-0013 §5 / §6).

它是整套解耦的落点: **注册在 EXECUTE_SHELL 这个能力上, 不是注册在 shell_run 这个工具
上**. 任何工具只要声明 EXECUTE_SHELL 并交出 ShellSubject, 就走同一条分析路径.

一次分析做六件事:

1. 按方言解析. 解析失败走 Hard Deny 预扫描, 再落 ASK —— 解析不出来绝不等于放行.
2. 结构化 Hard Deny 判定.
3. 展开并冻结目标集合. 展不开就标 DYNAMIC, 由策略层决定怎么办.
4. 从命令事实推导本次**实际**用到的能力, 把 shell_run 声明的最宽上界收缩到这些.
5. 解析每个可执行文件的身份. Agent 可写目录里的 executable 不能凭名字命中普通 Allow.
6. 受保护路径检查: 写入直接 Hard Deny, 凭证读取直接 Hard Deny.

产出经 effective_plan 回写. 收缩规则由 validate_narrowing 兜底, 这里多写一行放大能力
的代码也会被拦下来.

**这里只留"按什么顺序问"** (ADR-0028): 第 3 步的路径推导在 shell_effects, 第 5 步的
身份解析与状态绑定在 executable_binding. 两段的错法与这里不同 —— 前者错了会凭空造出
目标, 后者错了会把换掉的二进制当成原来那个 —— 因此各自独立测试.
"""

from __future__ import annotations

from dataclasses import replace

from forgecli.application.security.analyzers.executable_binding import bind_executables
from forgecli.application.security.analyzers.registry import (
    AnalysisFindings,
    AnalyzerStage,
    CapabilityAnalyzer,
)
from forgecli.application.security.analyzers.shell_effects import (
    NETWORK_TOOLS,
    effects_of,
    irreversible_detail,
    may_write,
    mentions_irreversible,
    unproven_units,
    unresolved_targets,
)
from forgecli.application.security.executable_resolver import ExecutableResolver
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.findings import RiskFact
from forgecli.domain.security.hard_deny import inspect_command, prefilter_raw
from forgecli.domain.security.shell.command_plan import CommandPlan, ShellKind
from forgecli.domain.security.shell.effects import runs_arbitrary_code
from forgecli.domain.security.shell.expansion import ExpansionResult, expand_targets
from forgecli.domain.security.shell.parser import parse_command
from forgecli.domain.security.shell.wrappers import indirectly_executes
from forgecli.domain.security.vocabulary import DecisionReason
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.plan import (
    DeclarationConfidence,
    PlanEffects,
    ShellSubject,
    TargetResolution,
    ToolPlan,
    WorkspaceScope,
)

__all__ = ["ShellCapabilityAnalyzer"]

# 写 / 删 / 移的命令清单在 domain.security.shell.effects: 那里的默认值是 UNPROVEN
# 而不是"只读", 而默认值的方向正是这份知识唯一要紧的性质.


class ShellCapabilityAnalyzer(CapabilityAnalyzer):
    def __init__(self, resolver: ExecutableResolver) -> None:
        self._resolver = resolver

    @property
    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.EXECUTE_SHELL})

    @property
    def stage(self) -> AnalyzerStage:
        # 它是事实的来源: 命令串在这里变成能力, 目标集合和可执行文件身份.
        return AnalyzerStage.DERIVE

    def analyze(
        self,
        findings: AnalysisFindings,
        policy: PolicyContext,
        context: ExecutionContext,
    ) -> AnalysisFindings:
        subject = findings.plan.analysis_subject
        if not isinstance(subject, ShellSubject):
            # 声明了 EXECUTE_SHELL 却拿不出原始命令: 无法分析, 走最保守路径.
            return findings.asked(
                DecisionReason.PARSE_INCOMPLETE,
                RiskFact(code="missing_subject", detail="缺少可分析的命令原文"),
            )

        command = parse_command(
            subject.raw_command,
            ShellKind(context.profile.shell_launch.dialect),
            cwd=context.cwd,
        )
        findings = findings.with_command(command)
        if not command.status.complete:
            return self._handle_incomplete(findings, command)

        hard = inspect_command(command)
        if hard is not None:
            return findings.denied(hard.reason, hard.fact)

        home = context.environment.get("HOME", "")
        expansion = expand_targets(
            command,
            resolve=context.resolve,
            glob=lambda pattern: context.filesystem.expand_glob(
                pattern, root=context.cwd
            ),
            home=home,
        )
        effects = effects_of(command, expansion.targets, home, resolve=context.resolve)
        capabilities = self._capabilities_of(command, effects, context)
        narrowed = self._narrow(
            findings.plan, capabilities, effects, expansion.resolution, context
        )
        result = findings.with_plan(narrowed)

        result = bind_executables(result, command, context, self._resolver)
        result = self._check_irreversible(result, command)
        result = self._check_target_closure(
            result, command, expansion, home, confined=policy.confined
        )
        return result

    # ---- 各段 ----

    def _handle_incomplete(
        self, findings: AnalysisFindings, command: CommandPlan
    ) -> AnalysisFindings:
        """解析失败或不完整: 先跑不依赖 AST 的 Hard Deny 预扫描, 再落 ASK."""
        hard = prefilter_raw(command.raw_command)
        if hard is not None:
            return findings.denied(hard.reason, hard.fact)
        detail = command.parse_error or "; ".join(command.opaque_constructs)
        return findings.asked(
            DecisionReason.PARSE_INCOMPLETE,
            RiskFact(
                code=f"parse_{command.status.value}",
                detail=detail or "命令结构无法完整解析",
            ),
        )

    def _check_target_closure(
        self,
        findings: AnalysisFindings,
        command: CommandPlan,
        expansion: ExpansionResult,
        home: str,
        *,
        confined: bool,
    ) -> AnalysisFindings:
        """目标集合封不封得住. 三条判据的**顺序就是根因优先级**.

        `asked()` 保留最先给出的理由, 而"不认识这个程序"是根因, "目标集合没封闭"是它的
        后果 —— 审计里该记根因.

        **有围栏时这三条只产出事实, 不产出 ASK** (ADR-0030 决策 1). 它们回答的都是
        "静态推不出这条命令会碰什么", 而围栏不需要推 —— 推不出来的后果退化成一次全量
        快照 (慢一点) 与一段不完整的说明, 不再是一次人工确认.

        风险事实照常记录: 审批界面与审计要用, 恢复层也要用它选 TARGETED 还是 FULL.
        """
        result = findings
        unresolved = unresolved_targets(command, home)
        if unresolved:
            # 目标里还有运行期才确定的引用. 读取也要拦: `cat $SECRET_PATH` 的真实目标
            # 只有跑起来才知道, 受保护路径检查对它无从下手.
            fact = RiskFact(
                code="unresolved_target",
                detail="目标含运行期引用: " + ", ".join(unresolved),
            )
            result = (
                result.with_risk(fact)
                if confined
                else result.asked(DecisionReason.UNRESOLVED_TARGET_SET, fact)
            )
        unproven = unproven_units(command)
        if unproven:
            # 认不出这个可执行文件会碰什么. 空的 write_paths 在这里**不构成**"它没写"
            # 的证据, 所以不能让它凭 WORKSPACE_READ 走只读快速路径.
            fact = RiskFact(
                code="unproven_executable",
                detail=(
                    "无法推导影响范围的命令: "
                    + ", ".join(unproven)
                    + " (位置参数已按读取记录, 但这不是完整清单)"
                ),
            )
            result = (
                result.with_risk(fact)
                if confined
                else result.asked(DecisionReason.UNPROVEN_EXECUTABLE, fact)
            )
        if not expansion.closed and may_write(command):
            # 目标集合没封闭还要写真实工作区: 不能给普通 ALLOW (ADR-0013 §6.2).
            reasons = expansion.reasons or ("未知原因",)
            fact = RiskFact(
                code="target_resolution",
                detail="目标集合无法静态封闭: " + "; ".join(reasons),
            )
            result = (
                result.with_risk(fact)
                if confined
                else result.asked(DecisionReason.UNRESOLVED_TARGET_SET, fact)
            )
        return result

    def _capabilities_of(
        self,
        command: CommandPlan,
        effects: PlanEffects,
        context: ExecutionContext,
    ) -> frozenset[Capability]:
        capabilities = {Capability.EXECUTE_SHELL, Capability.SPAWN_PROCESS}
        if command.scripts or any(runs_arbitrary_code(unit) for unit in command.units):
            # 有正文的走内容分析; 只是"会跑任意代码"但拿不到正文的 (`java -jar x.jar`,
            # `docker run`, `vim -c`) 同样要声明 EXECUTE_SCRIPT —— 否则脚本分析器根本
            # 不会被选中, 这条调用就凭 WORKSPACE_READ 走了只读快速路径.
            capabilities.add(Capability.EXECUTE_SCRIPT)
        if any(unit.name in NETWORK_TOOLS for unit in command.units):
            capabilities.add(Capability.NETWORK_ACCESS)
        if any(indirectly_executes(unit.name, unit.argv) for unit in command.units):
            # 判据是"这次有没有把控制权交出去", 不是"命令叫不叫 find".
            # `find . -name '*.java'` 一个进程都不起, 与 `ls -R` 同构; 而按名字判会让
            # 每一条纯列举都要人点头 (ADR-0024 拒绝命令名白名单的同一条理由).
            capabilities.add(Capability.EXECUTE_SCRIPT)
        for path in effects.write_paths:
            capabilities.add(_write_capability(path, context))
        for path in effects.delete_paths:
            capabilities.add(
                Capability.WORKSPACE_DELETE
                if _writable_in_workspace(path, context)
                else Capability.EXTERNAL_WRITE
            )
        if effects.move_pairs:
            capabilities.add(Capability.PATH_MOVE)
        for path in effects.read_paths:
            capabilities.add(
                Capability.WORKSPACE_READ
                if _in_workspace(path, context)
                else Capability.EXTERNAL_READ
            )
        if mentions_irreversible(command):
            capabilities.add(Capability.EXTERNAL_IRREVERSIBLE_EFFECT)
        if may_write(command) and not effects.mutating_targets:
            # 可能写, 但写在哪推导不出来. 仍然要声明写能力: mutates_workspace 是恢复层
            # 建不建恢复点的唯一判据, 不声明就等于让 `npm test` 与 `java -jar` 这类
            # 命令在没有任何恢复保障的情况下跑起来.
            capabilities.add(_write_capability(context.cwd, context))
        return frozenset(capabilities)

    def _narrow(
        self,
        plan: ToolPlan,
        capabilities: frozenset[Capability],
        effects: PlanEffects,
        resolution: TargetResolution,
        context: ExecutionContext,
    ) -> ToolPlan:
        """把 shell_run 的最宽上界收缩到本次实际的事实."""
        touched = (*effects.read_paths, *effects.mutating_targets)
        scope = (
            context.scope_of_all(touched) if touched else WorkspaceScope.IN_WORKSPACE
        )
        return replace(
            plan,
            capabilities=capabilities & plan.capabilities,
            effects=effects,
            target_resolution=resolution,
            workspace_scope=scope,
            declaration_confidence=DeclarationConfidence.DERIVED,
        )

    def _check_irreversible(
        self, findings: AnalysisFindings, command: CommandPlan
    ) -> AnalysisFindings:
        for unit in command.units:
            detail = irreversible_detail(unit)
            if detail is None:
                continue
            # Mandatory Ask: 缓存, 历史批准, 学习规则和 full_access 都满足不了它.
            return findings.asked(
                DecisionReason.EXTERNAL_IRREVERSIBLE_EFFECT,
                RiskFact(code="irreversible", detail=f"{detail}: {unit.raw}"),
                mandatory=True,
            )
        return findings


# ---- 能力映射 ----


def _in_workspace(path: str, context: ExecutionContext) -> bool:
    """读取视角: 授权目录算区内."""
    return context.scope_of(path) is not WorkspaceScope.OUTSIDE


def _writable_in_workspace(path: str, context: ExecutionContext) -> bool:
    """写入视角: 只读授权目录算区外, 写它要 EXTERNAL_WRITE 而不是 WORKSPACE_WRITE."""
    return context.scope_for_write(path) is not WorkspaceScope.OUTSIDE


def _write_capability(path: str, context: ExecutionContext) -> Capability:
    return (
        Capability.WORKSPACE_WRITE
        if _writable_in_workspace(path, context)
        else Capability.EXTERNAL_WRITE
    )
