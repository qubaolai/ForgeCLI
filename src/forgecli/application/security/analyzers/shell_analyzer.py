"""EXECUTE_SHELL 能力分析器 (ADR-0013 §5 / §6).

它是整套解耦的落点: **注册在 EXECUTE_SHELL 这个能力上, 不是注册在 shell.run 这个工具
上**. 任何工具只要声明 EXECUTE_SHELL 并交出 ShellSubject, 就走同一条分析路径.

一次分析做六件事:

1. 按方言解析. 解析失败走 Hard Deny 预扫描, 再落 ASK —— 解析不出来绝不等于放行.
2. 结构化 Hard Deny 判定.
3. 展开并冻结目标集合. 展不开就标 DYNAMIC, 由策略层决定怎么办.
4. 从命令事实推导本次**实际**用到的能力, 把 shell.run 声明的最宽上界收缩到这些.
5. 解析每个可执行文件的身份. Agent 可写目录里的 executable 不能凭名字命中普通 Allow.
6. 受保护路径检查: 写入直接 Hard Deny, 凭证读取直接 Hard Deny.

产出经 effective_plan 回写. 收缩规则由 validate_narrowing 兜底, 这里多写一行放大能力
的代码也会被拦下来.
"""

from __future__ import annotations

from dataclasses import replace
from fnmatch import fnmatch
from pathlib import PurePath, PurePosixPath

from forgecli.application.security.analyzers.registry import (
    AnalysisFindings,
    AnalyzerStage,
    CapabilityAnalyzer,
)
from forgecli.application.security.executable_resolver import ExecutableResolver
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.decision import RiskFact
from forgecli.domain.security.hard_deny import inspect_command, prefilter_raw
from forgecli.domain.security.protected_paths import ProtectedPathPolicy
from forgecli.domain.security.shell.arguments import classify_arguments
from forgecli.domain.security.shell.builtins import (
    dialect_has_closed_builtin_set,
    is_builtin,
)
from forgecli.domain.security.shell.command_plan import (
    CommandPlan,
    CommandUnit,
    ShellKind,
)
from forgecli.domain.security.shell.effects import (
    EffectKind,
    effect_kind_of,
    runs_arbitrary_code,
)
from forgecli.domain.security.shell.expansion import expand_home, expand_targets
from forgecli.domain.security.shell.parser import parse_command
from forgecli.domain.security.shell.wrappers import INDIRECT_EXECUTORS
from forgecli.domain.security.vocabulary import DecisionReason
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.hashing import digest
from forgecli.domain.tool.plan import (
    DeclarationConfidence,
    MovePair,
    PlanEffects,
    ShellSubject,
    TargetResolution,
    ToolPlan,
    WorkspaceScope,
)

__all__ = ["ShellCapabilityAnalyzer"]

# 会产生网络访问的命令. 判据仍是命令语义而不是名字白名单: 命中只是"声明 NETWORK_ACCESS
# 这个能力", 允不允许由规则层按目标裁决.
_NETWORK_TOOLS = frozenset(
    {
        "curl",
        "wget",
        "nc",
        "ncat",
        "netcat",
        "socat",
        "ssh",
        "scp",
        "rsync",
        "sftp",
        "ftp",
        "telnet",
        "Invoke-WebRequest",
        "iwr",
    }
)

# 不可逆外部副作用 (ADR-0013 §4.1). 命中即 Mandatory Ask, 所有模式一视同仁.
_IRREVERSIBLE: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("git", ("push",), "推送到远端"),
    ("npm", ("publish", "unpublish", "deprecate", "dist-tag"), "发布 npm 包"),
    ("yarn", ("publish",), "发布 npm 包"),
    ("pnpm", ("publish",), "发布 npm 包"),
    ("docker", ("push",), "推送镜像"),
    ("terraform", ("apply", "destroy"), "基础设施变更"),
    ("kubectl", ("apply", "delete", "drain"), "集群变更"),
    ("aws", ("deploy", "s3", "cloudformation"), "云端变更"),
    ("gh", ("release",), "发布 release"),
    ("cargo", ("publish",), "发布 crate"),
    ("twine", ("upload",), "发布 Python 包"),
)

# 写 / 删 / 移的命令清单已移到 domain.security.shell.effects: 那里的默认值是 UNPROVEN
# 而不是"只读", 而默认值的方向正是这份知识唯一要紧的性质.


class ShellCapabilityAnalyzer(CapabilityAnalyzer):
    def __init__(
        self,
        resolver: ExecutableResolver,
        protected_paths: ProtectedPathPolicy,
    ) -> None:
        self._resolver = resolver
        self._protected = protected_paths

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
            subject.raw_command, _dialect(subject.shell_kind), cwd=subject.cwd
        )
        if not command.status.complete:
            return self._handle_incomplete(findings, command)

        hard = inspect_command(command)
        if hard is not None:
            return findings.denied(hard.reason, hard.fact)

        home = context.environment.get("HOME", "")
        expansion = expand_targets(
            command,
            cwd=subject.cwd,
            glob=lambda pattern: context.filesystem.expand_glob(
                pattern, root=subject.cwd
            ),
            home=home,
        )
        effects = _effects_of(command, subject.cwd, expansion.targets, home)
        capabilities = self._capabilities_of(command, effects, context)
        narrowed = self._narrow(
            findings.plan, capabilities, effects, expansion.resolution, context
        )
        result = findings.with_plan(narrowed)

        result = self._check_protected_paths(result, effects)
        if result.hard_deny is not None:
            return result
        result = self._check_executables(result, command, context)
        result = self._check_irreversible(result, command)
        unresolved = _unresolved_targets(command, home)
        if unresolved:
            # 目标里还有运行期才确定的引用. 读取也要拦: `cat $SECRET_PATH` 的真实目标
            # 只有跑起来才知道, 受保护路径检查对它无从下手.
            result = result.asked(
                DecisionReason.UNRESOLVED_TARGET_SET,
                RiskFact(
                    code="unresolved_target",
                    detail="目标含运行期引用: " + ", ".join(unresolved),
                ),
            )
        # 这一支排在目标集合检查**之前**: 两条都会成立, 而 asked() 保留最先给出的理由.
        # "不认识这个程序"是根因, "目标集合没封闭"是它的后果 —— 审计里该记根因.
        unproven = _unproven_units(command)
        if unproven:
            # 认不出这个可执行文件会碰什么. 空的 write_paths 在这里**不构成**"它没写"
            # 的证据, 所以不能让它凭 WORKSPACE_READ 走只读快速路径.
            result = result.asked(
                DecisionReason.UNPROVEN_EXECUTABLE,
                RiskFact(
                    code="unproven_executable",
                    detail=(
                        "无法推导影响范围的命令: "
                        + ", ".join(unproven)
                        + " (位置参数已按读取记录, 但这不是完整清单)"
                    ),
                ),
            )
        if not expansion.closed and _may_write(command):
            # 目标集合没封闭还要写真实工作区: 不能给普通 ALLOW (ADR-0013 §6.2).
            result = result.asked(
                DecisionReason.UNRESOLVED_TARGET_SET,
                RiskFact(
                    code="target_resolution",
                    detail="目标集合无法静态封闭: "
                    + "; ".join(expansion.reasons or ("未知原因",)),
                ),
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
        if any(unit.name in _NETWORK_TOOLS for unit in command.units):
            capabilities.add(Capability.NETWORK_ACCESS)
        if any(unit.name in INDIRECT_EXECUTORS for unit in command.units):
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
        if _mentions_irreversible(command):
            capabilities.add(Capability.EXTERNAL_IRREVERSIBLE_EFFECT)
        if _may_write(command) and not effects.mutating_targets:
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
        """把 shell.run 的最宽上界收缩到本次实际的事实."""
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

    def _check_protected_paths(
        self, findings: AnalysisFindings, effects: PlanEffects
    ) -> AnalysisFindings:
        for path in effects.mutating_targets:
            root = self._protected.classify(path)
            if root is not None:
                return findings.denied(
                    DecisionReason.HARD_DENY_PROTECTED_PATH,
                    RiskFact(
                        code="protected_path",
                        detail=f"写入受保护路径 ({root.category.value}): {path}",
                    ),
                )
        for path in effects.read_paths:
            root = self._protected.classify(path)
            if root is not None and root.deny_read:
                return findings.denied(
                    DecisionReason.HARD_DENY_CREDENTIAL_ACCESS,
                    RiskFact(
                        code="protected_path",
                        detail=f"读取受保护内容 ({root.category.value}): {path}",
                    ),
                )
        return findings

    def _check_executables(
        self,
        findings: AnalysisFindings,
        command: CommandPlan,
        context: ExecutionContext,
    ) -> AnalysisFindings:
        result = findings
        # 逐个单元的身份按顺序合并: 一条管道里换掉任何一个二进制, 学习规则就不该再命中.
        identities: list[str] = []
        # 与 identities 平行的可读基名, 只给人看 (/rules 列表要靠它认出是哪条规则).
        names: list[str] = []
        # 内建命令不算在分母里: 它们本来就不是文件, 不该因为"PATH 上没有"而拖累身份判定.
        external = 0
        for unit in command.units:
            if not unit.executable:
                continue
            if is_builtin(unit.executable, command.shell_kind):
                # Shell 自己解释, 不查 PATH. `export FOO=1` 不是"找不到可执行文件".
                continue
            external += 1
            identity = self._resolver.resolve(unit.executable, context)
            if identity.realpath:
                identities.append(f"{identity.realpath}:{identity.content_hash}")
                names.append(PurePath(identity.realpath).name)
            if not identity.realpath:
                result = self._unresolved(result, unit.executable, command, context)
                continue
            if not identity.eligible_for_plain_allow:
                # 名字和系统工具一样也没用: 这个文件 Agent 自己能改.
                result = result.asked(
                    DecisionReason.SCRIPT_EXECUTION,
                    RiskFact(
                        code="agent_writable_executable",
                        detail=(
                            f"{unit.executable} 解析到 Agent 可写位置 "
                            f"{identity.realpath} ({identity.trust_zone.value})"
                        ),
                    ),
                )
        # 有任何一个解析不出来就不给身份哈希: 半份身份绑不住任何东西.
        if identities and len(identities) == external:
            result = result.with_identity(digest(tuple(identities)), tuple(names))
        return result

    @staticmethod
    def _unresolved(
        findings: AnalysisFindings,
        token: str,
        command: CommandPlan,
        context: ExecutionContext,
    ) -> AnalysisFindings:
        """既不是内建又不在受控 PATH 上. 分两种情况, 差别在于**能不能下这个结论**.

        方言的内建集合可枚举时 (POSIX / cmd), "不是内建 + 不在 PATH" 就等于跑不了, 判
        DENY: 批准它买不到任何东西, 因为执行用的是同一份受控 PATH. 典型场景是模型不知道
        自己在 Windows 上, 发了一条 `ls -al`.

        PowerShell 的 cmdlet 数以千计且可由模块动态注册, 枚举不出封闭集合 —— 那时"不在
        PATH 上"完全可能是一个正常的 cmdlet. 判不出来就别判死, 保持原来的 ASK.
        """
        if not dialect_has_closed_builtin_set(command.shell_kind):
            return findings.asked(
                DecisionReason.PARSE_INCOMPLETE,
                RiskFact(
                    code="executable_unresolved",
                    detail=f"受控 PATH 中找不到 {token}",
                ),
            )
        return findings.cannot_run(
            DecisionReason.EXECUTABLE_NOT_FOUND,
            RiskFact(
                code="executable_not_found",
                # 带上平台与方言: 模型多半是照着另一个平台的习惯发的命令, 光说
                # "找不到"它不知道该往哪个方向改.
                detail=(
                    f"{token}: 在 {context.profile.platform} 的 "
                    f"{command.shell_kind.value} 环境里既不是内建命令, "
                    "也不在受控 PATH 上"
                ),
            ),
        )

    def _check_irreversible(
        self, findings: AnalysisFindings, command: CommandPlan
    ) -> AnalysisFindings:
        for unit in command.units:
            detail = _irreversible_detail(unit)
            if detail is None:
                continue
            # Mandatory Ask: 缓存, 历史批准, 学习规则和 full_access 都满足不了它.
            return findings.asked(
                DecisionReason.EXTERNAL_IRREVERSIBLE_EFFECT,
                RiskFact(code="irreversible", detail=f"{detail}: {unit.raw}"),
                mandatory=True,
            )
        return findings


# ---- 纯函数 ----


def _dialect(shell_kind: str) -> ShellKind:
    try:
        return ShellKind(shell_kind)
    except ValueError:
        return ShellKind.POSIX


def _unresolved_targets(command: CommandPlan, home: str) -> tuple[str, ...]:
    """展开之后仍然无法确定的位置参数.

    只看被判定为路径的那些 (classify_arguments): `grep "$PATTERN" .` 里的 `$PATTERN`
    是搜索词, 它没展开不影响目标集合能不能封闭, 而按未解析目标报出去会让一条本可以
    封闭的命令白白掉进 DYNAMIC.
    """
    return tuple(
        dict.fromkeys(
            arg
            for unit in command.units
            for arg in classify_arguments(unit).paths
            if _unresolved(expand_home(arg, home))
        )
    )


def _effects_of(
    command: CommandPlan, cwd: str, expanded: tuple[str, ...], home: str = ""
) -> PlanEffects:
    """从命令事实推导副作用. 路径按已冻结的 cwd 规范化为绝对路径.

    glob 用 expand_targets 已经展开好的结果替换, 而不是把 `*.tmp` 原样记成一个目标 ——
    审批界面要展示的是"会删掉哪两个文件", 不是一个模式串.
    """
    writes = [_absolute(expand_home(t, home), cwd) for t in command.write_targets]
    reads = [_absolute(expand_home(t, home), cwd) for t in command.read_targets]
    deletes: list[str] = []
    moves: list[str] = []
    for unit in command.units:
        # 未解析的引用 ($VAR, %VAR%) 不进目标集合: 它们只会得到一个假的绝对路径.
        # 这类命令已经由展开层标成 DYNAMIC, 由策略层要求人类确认.
        # 哪些参数是路径由 classify_arguments 判, 不再用 `not startswith("-")` 一刀切.
        # 那条判据会把 grep 的搜索词, sed 的脚本, find 的 -name 模式全当成文件, 于是
        # 审批框里的"读取 (6 项)"有一半是凭空造的.
        positional = [
            resolved
            for arg in classify_arguments(unit).paths
            if not _unresolved(expand_home(arg, home))
            for resolved in _resolve_positional(expand_home(arg, home), cwd, expanded)
        ]
        kind = effect_kind_of(unit)
        if kind is EffectKind.DELETE:
            deletes.extend(positional)
        elif kind is EffectKind.MOVE:
            moves.extend(positional)
        elif kind is EffectKind.WRITE:
            writes.extend(positional)
        else:
            # READ / RUNS_CODE / UNPROVEN 的位置参数按**读取**记. 宁可多报:
            # `cat ~/.ssh/id_rsa` 的目标必须出现在 read_paths 里, 否则受保护路径检查
            # 根本看不到它.
            #
            # 但 UNPROVEN 的读取清单**不是完整清单**, 只是"至少这些". 完整性由
            # target_resolution=DYNAMIC 与 UNPROVEN_EXECUTABLE 这条 ASK 表达, 不能
            # 靠这里的空 write_paths 冒充"它什么都没写".
            reads.extend(positional)
    return PlanEffects(
        read_paths=tuple(dict.fromkeys(reads)),
        write_paths=tuple(dict.fromkeys(writes)),
        delete_paths=tuple(dict.fromkeys(deletes)),
        move_pairs=_move_pairs(moves),
        network_targets=_network_targets(command),
        external_effects=tuple(
            dict.fromkeys(
                detail
                for unit in command.units
                if (detail := _irreversible_detail(unit)) is not None
            )
        ),
        child_process=True,
        dynamic_execution=command.has_dynamic_execution,
    )


def _move_pairs(moves: list[str]) -> tuple[MovePair, ...]:
    """`mv a b c dir/` 的最后一项是目标, 其余是源."""
    if len(moves) < 2:
        return ()
    *sources, target = moves
    return tuple(MovePair(source=source, target=target) for source in sources)


def _network_targets(command: CommandPlan) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            arg
            for unit in command.units
            if unit.name in _NETWORK_TOOLS
            for arg in unit.argv
            if not arg.startswith("-") and ("://" in arg or "." in arg or "@" in arg)
        )
    )


def _resolve_positional(
    arg: str, cwd: str, expanded: tuple[str, ...]
) -> tuple[str, ...]:
    absolute = _absolute(arg, cwd)
    if not any(char in absolute for char in ("*", "?", "[")):
        return (absolute,)
    matched = tuple(item for item in expanded if fnmatch(item, absolute))
    # 展不开就保留原样: 上游已经把这种情况标成 DYNAMIC, 不能悄悄当成空集合.
    return matched or (absolute,)


def _absolute(target: str, cwd: str) -> str:
    path = PurePosixPath(target)
    return target if path.is_absolute() or not cwd else str(PurePosixPath(cwd) / path)


def _unresolved(target: str) -> bool:
    return any(marker in target for marker in ("$", "%", "~", "\x00sub"))


def _irreversible_detail(unit: CommandUnit) -> str | None:
    for executable, subcommands, detail in _IRREVERSIBLE:
        if unit.name != executable:
            continue
        if any(sub in unit.argv for sub in subcommands):
            return detail
    return None


def _mentions_irreversible(command: CommandPlan) -> bool:
    return any(_irreversible_detail(unit) is not None for unit in command.units)


_MAY_WRITE_KINDS = (
    EffectKind.WRITE,
    EffectKind.DELETE,
    EffectKind.MOVE,
    # 会跑任意代码的当然可能写 (`npm test` 写 coverage, `java -jar` 写什么都可以).
    EffectKind.RUNS_CODE,
    # 证明不了它不写, 就得当它会写.
    EffectKind.UNPROVEN,
)


def _may_write(command: CommandPlan) -> bool:
    """这条命令**有可能**改写东西吗.

    只有确定只读 (EffectKind.READ) 才算不写. 这个判据同时喂两处: 目标集合未封闭时要不要
    问人, 以及要不要声明写能力从而拿到恢复点.
    """
    return any(
        effect_kind_of(unit) in _MAY_WRITE_KINDS or unit.write_targets
        for unit in command.units
    )


def _unproven_units(command: CommandPlan) -> tuple[str, ...]:
    """影响范围推导不出来的可执行文件. 去重保序, 只留基名."""
    return tuple(
        dict.fromkeys(
            unit.name
            for unit in command.units
            if effect_kind_of(unit) is EffectKind.UNPROVEN
        )
    )


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
