"""阻塞工具线程、等人来解决的审批 broker: Web 与终端共用一个 (ADR-0045)。"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from forgecli.application.security.approval_service import ApprovalService
from forgecli.domain.security.approval import (
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalResponse,
)
from forgecli.domain.security.vocabulary import ApprovalScope


@dataclass
class _PendingApproval:
    request: ApprovalRequest
    ready: threading.Event = field(default_factory=threading.Event)
    response: ApprovalResponse | None = None
    release_note: str = ""


class BlockingApprovalBroker(ApprovalService):
    """一个 approval_id 只接受一次决议；未获决议一律 PENDING，绝不自动批准。

    **不设等待超时。** 审批的正确终止条件只有三个：用户做出决定、用户停止这一轮、服务
    退出。挂钟到点就把请求判成"没批准"，等于让用户去泡杯咖啡的功夫决定这次调用的命运
    —— 而模型收到的是"上一次请求未获授权"，随后整轮停摆，用户回来时既看不到审批卡片，
    也没有任何补救入口。三个真实终止条件都由 ``release_pending`` 显式触发。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, _PendingApproval] = {}

    def request(self, approval: ApprovalRequest) -> ApprovalResponse:
        pending = _PendingApproval(approval)
        with self._lock:
            self._pending[approval.approval_id] = pending
        pending.ready.wait()
        with self._lock:
            self._pending.pop(approval.approval_id, None)
        if pending.response is not None:
            return pending.response
        return ApprovalResponse(
            outcome=ApprovalOutcome.PENDING,
            approval_id=approval.approval_id,
            note=pending.release_note or "审批未完成",
        )

    def list_pending(self) -> tuple[dict[str, object], ...]:
        with self._lock:
            items = tuple(self._pending.values())
        result: list[dict[str, object]] = []
        for item in items:
            result.append(
                {
                    "approval_id": item.request.approval_id,
                    "mandatory": item.request.mandatory,
                    "view": item.request.view.to_payload(),
                }
            )
        return tuple(result)

    def resolve(self, approval_id: str, decision: str) -> bool:
        with self._lock:
            pending = self._pending.get(approval_id)
            if pending is None or pending.response is not None:
                return False
            request = pending.request
            if decision == "deny":
                response = ApprovalResponse(
                    outcome=ApprovalOutcome.DENIED,
                    approval_id=approval_id,
                    note="用户拒绝",
                )
            else:
                scopes = {
                    "once": ApprovalScope.ONCE,
                    "workspace": ApprovalScope.WORKSPACE,
                }
                scope = scopes.get(decision)
                if scope is None or scope not in request.view.allowed_scopes:
                    return False
                response = ApprovalResponse(
                    outcome=ApprovalOutcome.APPROVED,
                    approval_id=approval_id,
                    scope=scope,
                    note="用户批准",
                )
            pending.response = response
            pending.ready.set()
            return True

    def release_pending(self, note: str) -> None:
        """放开所有等待中的审批，按未获批准处理（取消这一轮 / 进程退出）。"""
        with self._lock:
            pending = tuple(self._pending.values())
        for item in pending:
            item.release_note = note
            item.ready.set()

    def close(self) -> None:
        self.release_pending("Forge 正在退出，审批未完成")
