"""可执行文件身份解析与执行前状态绑定 (ADR-0013 §5.1, ADR-0027 §2, ADR-0028).

从 ShellCapabilityAnalyzer 分出来的一段, 回答两个问题:

- **这条命令实际会跑哪个文件.** 名字和系统工具一样不算数 —— Agent 自己能写的目录里
  放一个叫 `git` 的文件, 名字匹配的规则照样命中.
- **那个文件在裁决之后有没有被换掉.** 身份与内容哈希进 FileStateBinding, 由
  ToolRuntime 在启动进程前统一复核.

解析不出来的处理分两种, 差别在于**能不能下这个结论** —— 见 `_unresolved`.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import PurePath

from forgecli.application.security.executable_resolver import ExecutableResolver
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.security.executable_identity import ExecutableIdentity
from forgecli.domain.security.findings import AnalysisFindings, RiskFact
from forgecli.domain.security.shell.builtins import (
    dialect_has_closed_builtin_set,
    is_builtin,
)
from forgecli.domain.security.shell.command_plan import CommandPlan
from forgecli.domain.security.vocabulary import DecisionReason
from forgecli.domain.tool.hashing import digest
from forgecli.domain.tool.plan import FileStateBinding

__all__ = ["bind_executables"]

# 解释器链的最大深度. 超过就当作解析不完整 —— 一条绕不完的 shebang 链本身就是异常.
_MAX_CHAIN_DEPTH = 8


def bind_executables(
    findings: AnalysisFindings,
    command: CommandPlan,
    context: ExecutionContext,
    resolver: ExecutableResolver,
) -> AnalysisFindings:
    result = findings
    # 逐个单元的身份按顺序合并: 一条管道里换掉任何一个二进制, 学习规则就不该再命中.
    identities: list[str] = []
    # 与 identities 平行的可读基名, 只给人看 (/rules 列表要靠它认出是哪条规则).
    names: list[str] = []
    bindings: list[FileStateBinding] = []
    # 内建命令不算在分母里: 它们本来就不是文件, 不该因为"PATH 上没有"而拖累身份判定.
    external_units = 0
    for unit in command.units:
        if not unit.executable:
            continue
        if is_builtin(unit.executable, command.shell_kind):
            # Shell 自己解释, 不查 PATH. `export FOO=1` 不是"找不到可执行文件".
            continue
        external_units += 1
        chain = _identity_chain(unit.executable, context, resolver)
        complete_chain = True
        for identity in chain:
            if not identity.realpath:
                result = _unresolved(result, identity.requested_token, command, context)
                complete_chain = False
                continue
            if not identity.content_complete:
                result = result.cannot_run(
                    DecisionReason.SCRIPT_CONTENT_UNAVAILABLE,
                    RiskFact(
                        code="executable_identity_incomplete",
                        detail=(
                            "无法完整绑定可执行文件内容，拒绝按不完整身份运行: "
                            f"{identity.realpath}"
                        ),
                    ),
                )
                complete_chain = False
                continue
            names.append(PurePath(identity.realpath).name)
            bindings.append(
                FileStateBinding(
                    path=identity.absolute_path,
                    realpath=identity.realpath,
                    file_identity=identity.file_identity,
                    size=identity.size,
                    mtime_ns=identity.mtime_ns,
                    content_hash=identity.content_hash,
                )
            )
            if not identity.eligible_for_plain_allow:
                # 名字和系统工具一样也没用: 这个文件 Agent 自己能改.
                result = result.asked(
                    DecisionReason.SCRIPT_EXECUTION,
                    RiskFact(
                        code="agent_writable_executable",
                        detail=(
                            f"{identity.requested_token} 解析到 Agent 可写位置 "
                            f"{identity.realpath} ({identity.trust_zone.value})"
                        ),
                    ),
                )
        if complete_chain:
            identities.append(digest(tuple(item.identity_hash for item in chain)))
    # 有任何一个解析不出来就不给身份哈希: 半份身份绑不住任何东西.
    if identities and len(identities) == external_units:
        result = result.with_identity(digest(tuple(identities)), tuple(names))
    if bindings:
        result = result.with_plan(
            replace(
                result.plan,
                file_state_bindings=_merge_bindings(
                    result.plan.file_state_bindings, tuple(bindings)
                ),
            )
        )
    return result


def _identity_chain(
    token: str, context: ExecutionContext, resolver: ExecutableResolver
) -> tuple[ExecutableIdentity, ...]:
    """解析主程序及 shebang/env 间接解释器, 全部进入同一执行绑定."""
    resolved: list[ExecutableIdentity] = []
    pending = [token]
    seen: set[str] = set()
    while pending and len(resolved) < _MAX_CHAIN_DEPTH:
        current = pending.pop(0)
        if current in seen:
            continue
        seen.add(current)
        identity = resolver.resolve(current, context)
        resolved.append(identity)
        if identity.realpath:
            pending.extend(identity.interpreter_chain)
    if pending:
        resolved.append(
            ExecutableIdentity.unresolved("interpreter-chain-depth-exceeded")
        )
    return tuple(resolved)


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
                "也不在受控 PATH 上. 受控 PATH 继承自启动 forge 的那个 shell, "
                "所以你在终端里跑得起来它就应该在 —— 跑不起来说明它确实没装, "
                "或者装它的目录不在启动 forge 时的 PATH 上"
            ),
        ),
    )


def _merge_bindings(
    current: tuple[FileStateBinding, ...], added: tuple[FileStateBinding, ...]
) -> tuple[FileStateBinding, ...]:
    """按 realpath 去重并稳定排序, 避免分析器顺序改变 plan_hash."""
    merged = {item.realpath: item for item in (*current, *added)}
    return tuple(merged[path] for path in sorted(merged))
