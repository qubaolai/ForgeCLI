"""审批服务: 把 ASK 变成一个阻塞的人类决定 (ADR-0013 §4).

ASK 是阻塞状态, 不是"返回错误让主模型自己决定要不要重试". 因此这里的默认实现是
``PendingApprovalService`` —— 没有接入交互界面时, 每个请求都停在 PENDING, 绝不自动放行.
非交互环境 (CI, 管道输入) 用的也是它.

主模型不能代替用户确认: 它只能放弃, 重写请求, 或主动把请求升级为 ASK.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from forgecli.domain.security.approval import (
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalResponse,
)

__all__ = ["ApprovalService", "PendingApprovalService"]


class ApprovalService(ABC):
    """人类审批入口. 实现方负责展示完整视图并等待决定."""

    @abstractmethod
    def request(self, approval: ApprovalRequest) -> ApprovalResponse:
        """阻塞等待人类决定. 超时或不可用时返回 PENDING, 不得返回 APPROVED."""


class PendingApprovalService(ApprovalService):
    """无人可问时的安全默认: 一律 PENDING."""

    def request(self, approval: ApprovalRequest) -> ApprovalResponse:
        return ApprovalResponse(
            outcome=ApprovalOutcome.PENDING,
            approval_id=approval.approval_id,
            note="当前环境无法进行交互式审批",
        )
