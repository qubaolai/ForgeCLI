"""EXECUTE_SCRIPT 能力分析器 (ADR-0013 §7 / §8 / §10).

裁决顺序照 §8 的流程图实现, 一步不换:

    收集脚本内容 -> 确定性 ScriptAnalyzer
      ├─ 命中硬性危险信号            -> DENY (不调 LLM)
      ├─ 能力受控且静态分析足够完整   -> 交给 mode 预算裁决
      └─ 内容不确定 / 有 opaque 构造 -> Background Safety Classifier -> 本地策略引擎

无沙箱环境下 (本次唯一的环境) 有一条额外硬性要求: **新脚本, 变更脚本和未知脚本必须
调用分类器**, 不能因为"静态分析没发现危险"就跳过. 这条写在 `_needs_classifier` 里.

内容哈希缓存 (§12) 在这里消费: 命中缓存省掉一次 LLM 调用, 但 Hard Deny 与当前策略检查
仍然每次都跑 —— 缓存是效率优化, 不是安全边界.
"""

from __future__ import annotations

from dataclasses import replace

from forgecli.application.security.analyzers.registry import (
    AnalysisFindings,
    AnalyzerStage,
    CapabilityAnalyzer,
    ScriptSnapshot,
)
from forgecli.application.security.classifier import (
    CLASSIFIER_PROFILE_VERSION,
    ClassifierRequest,
    FailSafeClassifier,
)
from forgecli.application.security.risk_cache import RiskCache
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.decision import RiskFact
from forgecli.domain.security.risk import RiskReport, risk_cache_key
from forgecli.domain.security.script_facts import ScriptFacts
from forgecli.domain.security.script_patterns import analyze_script_source
from forgecli.domain.security.shell.command_plan import ScriptPayload, ShellKind
from forgecli.domain.security.shell.parser import PARSER_VERSION, parse_command
from forgecli.domain.security.vocabulary import DecisionReason
from forgecli.domain.tool.capability import Capability, normalize_capability
from forgecli.domain.tool.plan import ShellSubject, ToolPlan

__all__ = ["ANALYZER_VERSION", "ScriptExecutionAnalyzer"]

ANALYZER_VERSION = "1"
_MAX_SCRIPT_BYTES = 512 * 1024


class ScriptExecutionAnalyzer(CapabilityAnalyzer):
    def __init__(
        self,
        classifier: FailSafeClassifier,
        *,
        cache: RiskCache | None = None,
    ) -> None:
        self._classifier = classifier
        # 用 is None 判断而不是 `or`: 空缓存的 __len__ 为 0, `or` 会把它当成
        # 未注入而另建一个, 于是注入的缓存永远是空的.
        self._cache = RiskCache() if cache is None else cache

    @property
    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.EXECUTE_SCRIPT})

    @property
    def stage(self) -> AnalyzerStage:
        # 与 Shell 分析器同属推导阶段: 它把脚本内容变成能力事实.
        return AnalyzerStage.DERIVE

    def analyze(
        self,
        findings: AnalysisFindings,
        policy: PolicyContext,
        context: ExecutionContext,
    ) -> AnalysisFindings:
        subject = findings.plan.analysis_subject
        if Capability.EXECUTE_SCRIPT not in findings.plan.capabilities:
            # Shell 分析器已经把能力收缩过了: 这条命令里根本没有脚本, 不该被当成
            # "内容收集不到的脚本"来问人. 注册在能力上的分析器要认收缩后的事实.
            return findings

        payloads = self._collect(findings, context)
        if not payloads:
            # 声明了脚本执行却拿不出内容: 无法分析, 交给人.
            return findings.asked(
                DecisionReason.SCRIPT_EXECUTION,
                RiskFact(
                    code="script_unavailable",
                    detail=f"要执行代码但拿不到内容: {_subject_summary(subject)}",
                ),
            )

        result = findings
        for facts, source, payload in payloads:
            # 快照先记下: 即便随后 Hard Deny, 审批与审计也该看到"被拦下的是哪段代码".
            result = result.with_scripts(
                ScriptSnapshot(
                    language=payload.language,
                    origin=payload.origin,
                    source=source,
                    path=payload.path,
                )
            )
            result = self._apply(result, facts, source, policy, context)
            if result.hard_deny is not None:
                return result
        return result

    # ---- 内容收集 ----

    def _collect(
        self, findings: AnalysisFindings, context: ExecutionContext
    ) -> tuple[tuple[ScriptFacts, str, ScriptPayload], ...]:
        """从 heredoc, 内联代码和脚本文件三条来源收集内容并做静态分析."""
        subject = findings.plan.analysis_subject
        payloads: list[ScriptPayload] = []
        if isinstance(subject, ShellSubject):
            command = parse_command(
                subject.raw_command, _dialect(subject.shell_kind), cwd=subject.cwd
            )
            payloads.extend(command.scripts)
        return tuple(self._facts_of(payload, context) for payload in payloads)

    def _facts_of(
        self, payload: ScriptPayload, context: ExecutionContext
    ) -> tuple[ScriptFacts, str, ScriptPayload]:
        source = payload.source
        incomplete = False
        if source is None and payload.path:
            absolute = context.resolve(payload.path.split()[0])
            path_facts = context.filesystem.facts(absolute)
            if path_facts.is_regular_file:
                source = context.filesystem.read_text(
                    path_facts.realpath, max_bytes=_MAX_SCRIPT_BYTES
                )
            else:
                # 入口存在但读不到 (npm test 这类间接入口), 内容不完整.
                incomplete = True
        if source is None:
            source = ""
            incomplete = True
        # 正文与事实一起返回: 分类器要读的是正文, 静态事实只是提示.
        return (
            analyze_script_source(source, payload.language, incomplete=incomplete),
            source,
            payload,
        )

    # ---- 逐份脚本裁决 ----

    def _apply(
        self,
        findings: AnalysisFindings,
        facts: ScriptFacts,
        source: str,
        policy: PolicyContext,
        context: ExecutionContext,
    ) -> AnalysisFindings:
        hard = facts.hard_signals
        if hard:
            # 明显危险的脚本可以不经 LLM 直接 DENY.
            return findings.denied(
                DecisionReason.HARD_DENY_SCRIPT_SIGNAL,
                *(RiskFact(code=signal.code, detail=signal.detail) for signal in hard),
            )

        result = findings.with_plan(
            _with_script_capabilities(
                findings.plan, facts, findings.declared_capabilities
            )
        )
        if not _needs_classifier(facts):
            return result

        report = self._classify(facts, source, policy, context)
        if report.usable and report.capabilities:
            # 分类器看到了静态分析没看出来的能力 (它读的是正文, 能认出间接调用).
            # 同样以工具声明的上界为界, 同样只能收紧不能放宽.
            result = result.with_plan(
                _with_reported_capabilities(
                    result.plan, report.capabilities, findings.declared_capabilities
                )
            )
        if not report.usable:
            # 分类器超时, 异常, 非法输出或低置信度 -> 阻塞式 ASK.
            return result.asked(
                DecisionReason.CLASSIFIER_UNAVAILABLE
                if report.failure is not None
                else DecisionReason.CLASSIFIER_LOW_CONFIDENCE,
                RiskFact(code="classifier", detail=report.summary),
            )
        if not report.auto_executable:
            return result.asked(
                DecisionReason.SCRIPT_EXECUTION,
                RiskFact(
                    code="classifier",
                    detail=f"{report.risk_level.value}: {report.summary}",
                ),
            )
        if facts.opaque_constructs or facts.dynamic_execution:
            # 分类器说"低风险"也不够. 静态分析已经指出脚本里有它看不透的部分 (编码内容,
            # eval, 动态导入), 而这些正是超出**任何**静态可见范围的构造 —— 分类器读到的
            # 同样只是那段读不懂的源码. 两个都看不透的判断加在一起不等于看透了.
            return result.asked(
                DecisionReason.SCRIPT_EXECUTION,
                RiskFact(
                    code="opaque_script",
                    detail="脚本含运行期才能确定的构造: "
                    + "; ".join(facts.opaque_constructs or ("动态求值",)),
                ),
            )
        return result.with_risk(
            RiskFact(code="classifier", detail=f"low risk: {report.summary}")
        )

    def _classify(
        self,
        facts: ScriptFacts,
        source: str,
        policy: PolicyContext,
        context: ExecutionContext,
    ) -> RiskReport:
        key = risk_cache_key(
            facts,
            command_hash=facts.content_hash,
            working_directory=context.cwd,
            policy_version=policy.policy_version,
            execution_profile_hash=policy.execution_profile_hash,
            shell_kind=context.profile.shell_launch.kind,
            analyzer_version=ANALYZER_VERSION,
            parser_version=PARSER_VERSION,
            classifier_profile_version=CLASSIFIER_PROFILE_VERSION,
            intent_scope_hash=policy.intent_scope_hash,
        )
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        report = self._classifier.classify(
            ClassifierRequest(
                # 正文必须交给分类器. 早先这里写死 script_source=None, 于是它只看得到
                # ScriptFacts —— 对"这段代码在干什么"的判断建立在一份从未读到的源码上,
                # 而 ScriptFacts 的静态信号本来就只发现风险, 不证明安全.
                command=source,
                script_facts=facts,
                script_source=source,
                user_intent_summary=policy.user_intent_summary,
                mode=policy.mode.value,
                isolation_level=context.profile.isolation_level.value,
            )
        )
        # 只缓存可用结论: 缓存一个"超时"没有意义, 还会让下次也直接超时.
        if report.usable:
            self._cache.put(key, report)
        return report


def _needs_classifier(facts: ScriptFacts) -> bool:
    """无沙箱环境下, 新脚本, 变更脚本与未知脚本一律要过分类器.

    这里不看"有没有发现危险": 静态分析的"未发现"不构成放行依据 (ADR-0013 §8).
    """
    return facts.needs_classifier or not facts.hard_signals


def _with_script_capabilities(
    plan: ToolPlan, facts: ScriptFacts, upper_bound: frozenset[Capability]
) -> ToolPlan:
    """把脚本事实并入能力集合, 以**工具声明的上界**为界.

    以前这里写的是 `& plan.capabilities`, 而 capabilities 就是从 `plan.capabilities`
    起算的 —— 并集再与自身求交恒等于自身, 于是四个事实一个也进不去. 后果不是"少一项
    能力": 脚本里的网络访问与凭证读取从此对模式预算和 NetworkAnalyzer 完全不可见.
    界必须是工具的声明上界, 那也是 validate_narrowing 的比较基准.
    """
    capabilities = set(plan.capabilities)
    if facts.child_process:
        capabilities.add(Capability.SPAWN_PROCESS)
    if facts.network_access:
        capabilities.add(Capability.NETWORK_ACCESS)
    if facts.credential_access:
        capabilities.add(Capability.CREDENTIAL_ACCESS)
    if facts.external_side_effect:
        capabilities.add(Capability.EXTERNAL_WRITE)
    return replace(plan, capabilities=frozenset(capabilities) & upper_bound)


def _dialect(shell_kind: str) -> ShellKind:
    try:
        return ShellKind(shell_kind)
    except ValueError:
        return ShellKind.POSIX


def _subject_summary(subject: object) -> str:
    """给人看的"这次要跑什么". 只取命令原文的第一行且截断: 它是不可信输入."""
    raw = getattr(subject, "raw_command", None)
    if not isinstance(raw, str) or not raw.strip():
        return "未知入口"
    first = raw.strip().splitlines()[0]
    return first if len(first) <= 120 else f"{first[:120]}…"


def _with_reported_capabilities(
    plan: ToolPlan, reported: tuple[str, ...], upper_bound: frozenset[Capability]
) -> ToolPlan:
    """把分类器报告的能力并进计划, 以工具声明的上界为界.

    分类器是不可信输入, 但它**只能让结论更严**: 并集之后与上界求交, 不认识的能力名归一
    成 UNKNOWN 而 UNKNOWN 从来不在任何工具的上界里, 所以它既不能凭空造出新权限, 也不能
    因为拼错一个能力名就把请求打死.
    """
    capabilities = set(plan.capabilities)
    capabilities.update(normalize_capability(name) for name in reported)
    return replace(plan, capabilities=frozenset(capabilities) & upper_bound)
