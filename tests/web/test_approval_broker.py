"""Web 审批不能因为挂钟到点就判成"没批准".

回归来自一次真实故障: 用户在读一条很长的 shell 命令, 5 分钟的等待超时先到, broker 返回
PENDING, 模型收到"上一次请求未获授权"并停止整轮. 用户回来时审批卡片已经消失, 也没有任何
补救入口 —— 他既没同意也没拒绝, 系统却替他做了决定.

正确的终止条件只有三个: 用户决定, 用户停止这一轮, 服务退出.
"""

from __future__ import annotations

import threading

from forgecli.domain.security.approval import ApprovalOutcome, ApprovalRequest
from forgecli.domain.security.vocabulary import ApprovalScope
from forgecli.interfaces.web.approval import WebApprovalBroker


def _request(approval_id: str = "ap-1") -> ApprovalRequest:
    from forgecli.domain.security.approval import ApprovalPresentation

    return ApprovalRequest(
        approval_id=approval_id,
        binding=None,  # type: ignore[arg-type]
        presentation=ApprovalPresentation(
            action_summary="执行 shell 命令",
            raw_command="rm -rf /tmp/demo",
            allowed_scopes=(ApprovalScope.ONCE,),
        ),
        mandatory=True,
    )


def _ask(broker: WebApprovalBroker, request: ApprovalRequest):  # type: ignore[no-untyped-def]
    box: dict[str, object] = {}
    worker = threading.Thread(
        target=lambda: box.update(response=broker.request(request))
    )
    worker.start()
    return worker, box


def test_a_pending_approval_waits_instead_of_expiring() -> None:
    broker = WebApprovalBroker()
    worker, box = _ask(broker, _request())

    # 远超原来的 5 分钟超时不可能在这里等到; 用"还活着"证明它不会自己到点放弃。
    worker.join(timeout=0.4)
    assert worker.is_alive(), "审批不该因为等待时间到了就判成没批准"
    assert len(broker.list_pending()) == 1

    broker.resolve("ap-1", "once")
    worker.join(timeout=2.0)
    assert not worker.is_alive()
    assert box["response"].outcome is ApprovalOutcome.APPROVED  # type: ignore[union-attr]


def test_stopping_the_turn_releases_the_waiting_approval() -> None:
    """无超时等待必须有出口: 否则"停止"按不动一个正在等审批的 turn。"""
    broker = WebApprovalBroker()
    worker, box = _ask(broker, _request("ap-2"))
    worker.join(timeout=0.2)

    broker.release_pending("用户停止了这一轮，审批未完成")

    worker.join(timeout=2.0)
    assert not worker.is_alive()
    response = box["response"]
    assert response.outcome is ApprovalOutcome.PENDING  # type: ignore[union-attr]
    assert "停止" in response.note  # type: ignore[union-attr]


def test_shutdown_never_turns_into_an_approval() -> None:
    broker = WebApprovalBroker()
    worker, box = _ask(broker, _request("ap-3"))
    worker.join(timeout=0.2)

    broker.close()

    worker.join(timeout=2.0)
    assert box["response"].outcome is ApprovalOutcome.PENDING  # type: ignore[union-attr]
