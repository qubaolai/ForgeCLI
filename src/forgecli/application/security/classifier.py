"""Background Safety Classifier 的接口与 fail-safe 包装 (ADR-0013 §10 / §11).

分类器负责分析, **不拥有最终授权权**. 它给出的是风险事实, 由本地策略引擎裁决.

三条不能松的约束:

1. 任何失败都归一为"不可用", 而不可用一律 ASK. 不存在"调用失败所以放行".
2. 重试有上限. 反复调用直到拿到一个 allow 是绕过, 不是重试.
3. 脚本内容, 仓库文件和命令输出都是**不可信输入**. 它们不能改变分类器的系统策略,
   也不能声称"用户已经授权".
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

from forgecli.domain.security.risk import (
    ClassifierFailure,
    RiskLevel,
    RiskReport,
)
from forgecli.domain.security.script_facts import ScriptFacts

__all__ = [
    "ClassifierRequest",
    "FakeSafetyClassifier",
    "SafetyClassifier",
    "SafeClassifierGateway",
    "UnavailableSafetyClassifier",
]

DEFAULT_CONFIDENCE_THRESHOLD = 0.7
DEFAULT_MAX_ATTEMPTS = 2


@dataclass(frozen=True)
class ClassifierRequest:
    """交给分类器的材料. 全部是不可信输入, 分类器只读不信."""

    command: str
    script_facts: ScriptFacts | None
    script_source: str | None
    user_intent_summary: str
    mode: str
    isolation_level: str

    @property
    def truncated(self) -> bool:
        """材料本身就不完整时, 分类器给不出可信结论."""
        return self.script_facts is not None and self.script_facts.incomplete


class SafetyClassifier(ABC):
    """风险分类器. 实现方可以抛异常, 由 SafeClassifierGateway 兜住."""

    @abstractmethod
    def classify(self, request: ClassifierRequest) -> RiskReport:
        """给出结构化风险结论."""


class FakeSafetyClassifier(SafetyClassifier):
    """默认测试用分类器: 结论由构造参数决定, 不联网, 确定性输出."""

    def __init__(
        self,
        report: RiskReport | None = None,
        *,
        raises: Exception | None = None,
    ) -> None:
        self._report = report
        self._raises = raises
        self.calls: list[ClassifierRequest] = []

    def classify(self, request: ClassifierRequest) -> RiskReport:
        self.calls.append(request)
        if self._raises is not None:
            raise self._raises
        return self._report or RiskReport(
            risk_level=RiskLevel.LOW,
            confidence=0.95,
            intent_aligned=True,
            summary="fake classifier: 未发现风险",
            recommendation="allow",
        )


class UnavailableSafetyClassifier(SafetyClassifier):
    """没有接分类器时的实现: 永远返回"不可用".

    生产装配必须用它而不是 `FakeSafetyClassifier`. 那个 fake 默认返回
    LOW / intent_aligned / allow, 于是 `auto_executable` 为真 —— "没接分类器"会变成
    "分类器说没问题", 这是整条链路上最容易被忽略的一处 fail-open. 测试要一个确定的
    低风险结论时应显式构造 FakeSafetyClassifier 并注入.
    """

    def classify(self, request: ClassifierRequest) -> RiskReport:
        return RiskReport.unavailable(
            ClassifierFailure.NOT_CONFIGURED,
            "本次部署未配置风险分类器, 脚本无法评估",
        )


class SafeClassifierGateway:
    """把任意分类器包成"永远不会因为失败而放行"的样子.

    它不是重试器: 上限之内的重试只针对**可重试的传输失败**, 低置信度和非法输出不重试
    —— 同一份输入再问一遍不会变得更可信, 只会多烧一次预算.
    """

    def __init__(
        self,
        classifier: SafetyClassifier,
        *,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        on_event: Callable[[str, RiskReport], None] | None = None,
    ) -> None:
        self._classifier = classifier
        self._threshold = confidence_threshold
        self._max_attempts = max(1, max_attempts)
        self._on_event = on_event

    def classify(self, request: ClassifierRequest) -> RiskReport:
        if request.truncated:
            return self._emit(
                "context_truncated",
                RiskReport.unavailable(
                    ClassifierFailure.CONTEXT_TRUNCATED, "脚本内容收集不完整"
                ),
            )
        last: RiskReport | None = None
        for attempt in range(self._max_attempts):
            try:
                report = self._classifier.classify(request)
            except TimeoutError:
                last = RiskReport.unavailable(ClassifierFailure.TIMEOUT)
                continue
            except Exception as exc:  # noqa: BLE001 - 任何异常都必须归一为不可用
                last = RiskReport.unavailable(
                    ClassifierFailure.TRANSPORT_ERROR, str(exc)
                )
                continue
            if not report.usable:
                return self._emit("classifier_failed", report)
            if report.confidence < self._threshold:
                # 低置信度不重试: 同一份输入再问一遍不会更可信.
                return self._emit(
                    "low_confidence",
                    RiskReport.unavailable(
                        ClassifierFailure.LOW_CONFIDENCE,
                        f"置信度 {report.confidence:.2f} 低于阈值 {self._threshold}",
                    ),
                )
            return self._emit(f"ok_attempt_{attempt + 1}", report)
        return self._emit(
            "retry_exhausted",
            last or RiskReport.unavailable(ClassifierFailure.RETRY_EXHAUSTED),
        )

    def _emit(self, event: str, report: RiskReport) -> RiskReport:
        if self._on_event is not None:
            self._on_event(event, report)
        return report
