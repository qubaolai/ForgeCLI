"""风险分类器的输入输出契约 (ADR-0013 §11).

分类器**只输出结构化风险事实和建议**, 不能直接改策略, 不能调工具, 也不能覆盖 Hard Deny.
它的 `recommendation` 是本地策略引擎的一个输入, 不是结论.

fail-safe 是这份契约里最重要的部分: 超时, 传输错误, 非法输出, 低置信度和上下文截断
全部归一为"不可用", 而不可用一律进 ASK. 一个"分类器挂了所以放行"的分支就能让整套机制
失效.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from forgecli.domain.security.script_facts import ScriptFacts
from forgecli.domain.tool.hashing import digest

__all__ = [
    "ClassifierFailure",
    "RiskLevel",
    "RiskReport",
    "classifier_output_schema",
    "parse_risk_report",
]


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class ClassifierFailure(Enum):
    """分类器不可用的原因. 每一种的结果都一样: ASK."""

    TIMEOUT = "timeout"
    TRANSPORT_ERROR = "transport_error"
    INVALID_OUTPUT = "invalid_output"
    LOW_CONFIDENCE = "low_confidence"
    CONTEXT_TRUNCATED = "context_truncated"
    RETRY_EXHAUSTED = "retry_exhausted"
    # 这套部署根本没接分类器. 与"接了但这次失败"分开记: 前者是装配问题, 运维要看到它,
    # 而两者的裁决后果相同 —— ASK.
    NOT_CONFIGURED = "not_configured"


@dataclass(frozen=True)
class RiskReport:
    """分类器的结构化结论."""

    risk_level: RiskLevel
    confidence: float
    intent_aligned: bool
    summary: str
    capabilities: tuple[str, ...] = ()
    opaque_regions: tuple[str, ...] = ()
    recommendation: str = "ask"
    failure: ClassifierFailure | None = None
    classifier_version: str = "fake-1"

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence 必须在 [0, 1] 之间")

    @property
    def usable(self) -> bool:
        return self.failure is None

    @property
    def auto_executable(self) -> bool:
        """能不能支持自动执行. 注意这只是**必要条件之一**, 不是充分条件.

        最终裁决仍由本地策略引擎作出: Hard Deny, Mandatory Ask, 模式预算和目标集合封闭
        都在它之上.

        `recommendation` 必须一并检查. 早先这里只看 risk_level, 于是一份
        `risk_level=low` 且 `recommendation=deny` 的报告照样算可自动执行 —— 分类器明确
        说了别跑, 而结论里没人听. 分类器自相矛盾时按更严的那一项算.
        """
        return (
            self.usable
            and self.risk_level is RiskLevel.LOW
            and self.intent_aligned
            and not self.opaque_regions
            and self.recommendation.strip().lower() == "allow"
        )

    @classmethod
    def unavailable(cls, failure: ClassifierFailure, detail: str = "") -> RiskReport:
        return cls(
            risk_level=RiskLevel.UNKNOWN,
            confidence=0.0,
            intent_aligned=False,
            summary=detail or f"分类器不可用: {failure.value}",
            recommendation="ask",
            failure=failure,
        )


def classifier_output_schema() -> dict[str, object]:
    """分类器结构化输出的 JSON Schema. 不符合就是 INVALID_OUTPUT."""
    return {
        "type": "object",
        "properties": {
            "risk_level": {
                "type": "string",
                "enum": [level.value for level in RiskLevel],
            },
            "confidence": {"type": "number"},
            "intent_alignment": {
                "type": "string",
                "enum": ["aligned", "unclear", "misaligned"],
            },
            "capabilities": {"type": "array", "items": {"type": "string"}},
            "opaque_regions": {"type": "array", "items": {"type": "string"}},
            "recommendation": {
                "type": "string",
                "enum": ["allow", "ask", "deny"],
            },
            "summary": {"type": "string"},
        },
        "required": ["risk_level", "confidence", "intent_alignment", "recommendation"],
        "additionalProperties": True,
    }


def parse_risk_report(payload: object, *, version: str) -> RiskReport:
    """把分类器返回的 JSON 变成 RiskReport. 任何不合法都归 INVALID_OUTPUT."""
    if not isinstance(payload, dict):
        return RiskReport.unavailable(ClassifierFailure.INVALID_OUTPUT, "输出不是对象")
    try:
        level = RiskLevel(str(payload.get("risk_level", "")))
        confidence = float(payload.get("confidence", 0.0))
    except (ValueError, TypeError):
        return RiskReport.unavailable(
            ClassifierFailure.INVALID_OUTPUT, "risk_level 或 confidence 非法"
        )
    if not 0.0 <= confidence <= 1.0:
        return RiskReport.unavailable(
            ClassifierFailure.INVALID_OUTPUT, "confidence 超出取值范围"
        )
    return RiskReport(
        risk_level=level,
        confidence=confidence,
        intent_aligned=str(payload.get("intent_alignment", "")) == "aligned",
        summary=str(payload.get("summary", "")),
        capabilities=_strings(payload.get("capabilities")),
        opaque_regions=_strings(payload.get("opaque_regions")),
        recommendation=str(payload.get("recommendation", "ask")),
        classifier_version=version,
    )


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value)


def risk_cache_key(
    facts: ScriptFacts,
    *,
    command_hash: str,
    working_directory: str,
    policy_version: str,
    execution_profile_hash: str,
    shell_kind: str,
    parser_version: str,
    analyzer_version: str,
    classifier_profile_version: str,
    intent_scope_hash: str,
) -> str:
    """ADR-0013 §12 的完整缓存键.

    少一项都会让缓存在本该失效的时候命中. 例如漏掉 policy_version, 改完规则之后所有
    旧结论会继续生效.
    """
    return digest(
        {
            "script": facts.cache_key,
            "command_hash": command_hash,
            "working_directory": working_directory,
            "policy_version": policy_version,
            "execution_profile_hash": execution_profile_hash,
            "shell_kind": shell_kind,
            "parser_version": parser_version,
            "script_analyzer_version": analyzer_version,
            "classifier_profile_version": classifier_profile_version,
            "intent_scope_hash": intent_scope_hash,
        }
    )
