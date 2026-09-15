"""跳出循环机制：统一 LoopStopReason 与分类（ADR-0010 §6）。

所有退出、暂停和中断都必须通过 LoopStopReason 表达——这是循环唯一的出口词汇，
让「为什么停」可审计、可分类、可测试。分类规则决定一次停止是正常完成、可恢复暂停、
本轮阻塞停止，还是由预算策略决定（BUDGET_EXHAUSTED）。

枚举只收循环真的会产出的原因（ADR-0028 规则 C）：一个没人产出的停止原因读起来
像是一条已经接好的退出路径。
"""

from __future__ import annotations

from enum import Enum


class LoopStopReason(Enum):
    """循环停止原因，值即落盘字符串（ADR-0010 §6 第一版全集）。"""

    FINAL_ANSWER = "final_answer"
    # 工具声明本次输出需要人裁决 (ADR-0023). 与 WAIT_APPROVAL 分开: 那是"这次调用
    # 许不许可", 这是"这个方向对不对" —— 前者产生 ExecutionAuthorization, 后者不产生.
    WAIT_PLAN_REVIEW = "wait_plan_review"
    USER_CANCELLED = "user_cancelled"
    BUDGET_EXHAUSTED = "budget_exhausted"
    POLICY_DENIED = "policy_denied"
    CONTEXT_COMPACTION_REQUIRED = "context_compaction_required"
    MODEL_ERROR_BLOCKING = "model_error_blocking"

    @property
    def classification(self) -> StopClassification:
        """按 ADR-0010 §6 的分类规则归类。"""
        return _CLASSIFICATION[self]


class StopClassification(Enum):
    """停止原因的语义分类

    NORMAL              正常完成（FINAL_ANSWER）。
    RESUMABLE_PAUSE     可恢复暂停：等计划评审 / 等上下文压缩。
    BLOCKING            本轮阻塞停止，但 session 仍可继续。
    POLICY_DEPENDENT    是否可恢复由预算策略决定（BUDGET_EXHAUSTED）。
    """

    NORMAL = "normal"
    RESUMABLE_PAUSE = "resumable_pause"
    BLOCKING = "blocking"
    POLICY_DEPENDENT = "policy_dependent"


_CLASSIFICATION: dict[LoopStopReason, StopClassification] = {
    LoopStopReason.FINAL_ANSWER: StopClassification.NORMAL,
    LoopStopReason.WAIT_PLAN_REVIEW: StopClassification.RESUMABLE_PAUSE,
    LoopStopReason.CONTEXT_COMPACTION_REQUIRED: StopClassification.RESUMABLE_PAUSE,
    LoopStopReason.USER_CANCELLED: StopClassification.BLOCKING,
    LoopStopReason.POLICY_DENIED: StopClassification.BLOCKING,
    LoopStopReason.MODEL_ERROR_BLOCKING: StopClassification.BLOCKING,
    LoopStopReason.BUDGET_EXHAUSTED: StopClassification.POLICY_DEPENDENT,
}

# 自检：分类映射必须覆盖枚举全集，缺一即导入期报错。
assert set(_CLASSIFICATION) == set(LoopStopReason), "LoopStopReason 分类未覆盖全集"
