"""把要执行的脚本正文读进来, 变成 TOCTOU 锚点与审批展示材料 (ADR-0030 决策 7).

**它不分析脚本做了什么.** 前身 `ScriptExecutionAnalyzer` 有 384 行, 其中绝大部分是
风险模式匹配, LLM 分类器调用与从正文推导能力 —— 那些回答的都是"这段代码会做什么",
而围栏不需要问: 它跑在里面, 越界的访问由内核拒绝.

删掉那些之后剩下的就是这一件事: **记住这次读到的是哪一份正文**, 产出

- `FileStateBinding`: 执行前状态复核的锚点 (ADR-0027). 裁决时读到的文件, 执行时必须
  还是同一个.
- `ScriptSnapshot`: 审批展示, 学习规则绑定与审计要用 (见 domain/security/scripts.py).

两者围栏都替代不了 —— 围栏管"能碰到什么", 不管"批准的和跑起来的是不是同一份".
"""

from __future__ import annotations

from dataclasses import replace

from forgecli.application.security.analyzers.registry import (
    AnalyzerStage,
    CapabilityAnalyzer,
)
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.findings import AnalysisFindings, RiskFact
from forgecli.domain.security.scripts import ScriptSnapshot
from forgecli.domain.security.shell.command_plan import ScriptPayload
from forgecli.domain.security.vocabulary import DecisionReason
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.hashing import digest_bytes
from forgecli.domain.tool.plan import FileStateBinding

__all__ = ["ScriptBindingAnalyzer"]

# 读多少字节用来算内容哈希. 超过这个大小就不建绑定 —— 而不是把命令拒掉:
# 分析上限是本模块的实现限制, 不是那条命令的性质. 大脚本照样能跑, 只是没有 TOCTOU 锚点,
# 这一点如实记成风险事实.
_MAX_BINDING_BYTES = 512 * 1024


class ScriptBindingAnalyzer(CapabilityAnalyzer):
    @property
    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.EXECUTE_SCRIPT})

    @property
    def stage(self) -> AnalyzerStage:
        return AnalyzerStage.DERIVE

    def analyze(
        self,
        findings: AnalysisFindings,
        policy: PolicyContext,
        context: ExecutionContext,
    ) -> AnalysisFindings:
        if Capability.EXECUTE_SCRIPT not in findings.plan.capabilities:
            # Shell 分析器已经把能力收缩过了: 这条命令里根本没有脚本.
            return findings
        plan = findings.command_plan
        if plan is None:
            return findings

        result = findings
        for payload in plan.scripts:
            result = self._bind(result, payload, context)
            if result.unrunnable is not None:
                return result
        return result

    def _bind(
        self,
        findings: AnalysisFindings,
        payload: ScriptPayload,
        context: ExecutionContext,
    ) -> AnalysisFindings:
        source = payload.source
        binding: FileStateBinding | None = None

        if source is None and payload.path:
            absolute = context.resolve(payload.path)
            facts = context.filesystem.facts(absolute)
            if not facts.is_regular_file:
                # 早失败, 且说清是哪个文件. 这条命令跑起来也会失败, 不如现在就说.
                return findings.cannot_run(
                    DecisionReason.SCRIPT_CONTENT_UNAVAILABLE,
                    RiskFact(
                        code="script_content_unavailable",
                        detail=f"脚本不存在或不是普通文件: {absolute}",
                    ),
                )
            raw = context.filesystem.read_bytes(
                facts.realpath, max_bytes=_MAX_BINDING_BYTES + 1
            )
            if len(raw) > _MAX_BINDING_BYTES or len(raw) != facts.size:
                return findings.with_risk(
                    RiskFact(
                        code="script_binding_unavailable",
                        detail=(
                            f"脚本超过 {_MAX_BINDING_BYTES} 字节或读取不完整, "
                            f"无法建立执行前状态复核锚点: {absolute}"
                        ),
                    )
                )
            source = raw.decode("utf-8", errors="replace")
            binding = FileStateBinding(
                path=absolute,
                realpath=facts.realpath,
                file_identity=facts.file_identity,
                size=facts.size,
                mtime_ns=facts.mtime_ns,
                content_hash=digest_bytes(raw),
            )

        if source is None:
            # 内联脚本却拿不到正文: 记下来, 但不拦 —— 围栏不在乎我们看没看见.
            return findings.with_risk(
                RiskFact(
                    code="script_source_unavailable",
                    detail=f"拿不到 {payload.origin} 脚本的正文",
                )
            )

        result = findings
        if binding is not None:
            result = result.with_plan(
                replace(
                    result.plan,
                    file_state_bindings=_merge_bindings(
                        result.plan.file_state_bindings, (binding,)
                    ),
                )
            )
        return result.with_scripts(
            ScriptSnapshot(
                language=payload.language,
                origin=payload.origin,
                source=source,
                path=payload.path,
            )
        )


def _merge_bindings(
    current: tuple[FileStateBinding, ...], added: tuple[FileStateBinding, ...]
) -> tuple[FileStateBinding, ...]:
    """按 path 去重. 同一个脚本在一条命令里出现两次不该产生两条绑定."""
    seen = {binding.path: binding for binding in current}
    for binding in added:
        seen.setdefault(binding.path, binding)
    return tuple(seen.values())
