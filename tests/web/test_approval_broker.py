"""Web 审批不能因为挂钟到点就判成"没批准".

回归来自一次真实故障: 用户在读一条很长的 shell 命令, 5 分钟的等待超时先到, broker 返回
PENDING, 模型收到"上一次请求未获授权"并停止整轮. 用户回来时审批卡片已经消失, 也没有任何
补救入口 —— 他既没同意也没拒绝, 系统却替他做了决定.

正确的终止条件只有三个: 用户决定, 用户停止这一轮, 服务退出.
"""

from __future__ import annotations

import threading

from forgecli.domain.intents import SessionMode
from forgecli.domain.security.approval import (
    ApprovalBinding,
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalView,
)
from forgecli.domain.security.scripts import ScriptSnapshot
from forgecli.domain.security.vocabulary import ApprovalScope
from forgecli.interfaces.web.approval import WebApprovalBroker
from support.fakes import tool_plan


def _request(approval_id: str = "ap-1") -> ApprovalRequest:
    view = ApprovalView(
        plan=tool_plan(raw_command="rm -rf /tmp/demo"),
        action_summary="执行 shell 命令",
        allowed_scopes=(ApprovalScope.ONCE,),
    )
    return ApprovalRequest(
        approval_id=approval_id,
        binding=ApprovalBinding(
            plan_hash=view.plan.plan_hash,
            catalog_snapshot_hash="catalog",
            execution_profile_hash="profile",
            policy_version="1",
            mode=SessionMode.ACCEPT_EDITS,
            view_hash=view.view_hash,
        ),
        view=view,
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


def test_the_payload_carries_every_key_the_card_renders() -> None:
    """审批卡片渲染的每一个键都必须真的在 payload 里, 名字也要对得上.

    回归来自一次静默失效: 前端把脚本正文的字段声明成 ``content``, 而后端送的是
    ``source``. 取到的一直是 ``undefined``, 页面照常渲染, 没有任何东西报错 —— 用户看到
    的是一张不显示脚本正文的审批卡片, 而那正是他要批准的东西.

    这类缺陷靠肉眼验收发现不了: 少一个键的表现是"少一块内容", 而没有人记得本该有那块.
    """
    view = ApprovalView(
        plan=tool_plan(raw_command="bash clean.sh"),
        action_summary="执行 shell 命令",
        allowed_scopes=(ApprovalScope.ONCE,),
        learn_blocked_reason="拿不到可执行文件身份, 规则绑不住",
    )
    payload = view.to_payload()

    # 卡片直接读的顶层键 (web/src/approvalModel.ts 的 ApprovalView).
    assert set(payload) == {
        "mode",
        "tool_name",
        "workspace_roots",
        "raw_command",
        "target_resolution",
        "target_groups",
        "script_snapshots",
        "content_previews",
        "counts",
        "unresolved_reason",
        "allowed_scopes",
        "learn_blocked_reason",
    }

    # counts 含为零的类别: "网络 0" 与"没提网络"对读者不是一回事.
    counts = payload["counts"]
    assert isinstance(counts, list)
    assert [item["label"] for item in counts] == [
        "读取",
        "写入",
        "删除",
        "移动",
        "网络",
        "外部副作用",
    ]

    assert payload["learn_blocked_reason"] == "拿不到可执行文件身份, 规则绑不住"


def test_a_script_snapshot_reaches_the_card_under_the_name_it_renders() -> None:
    """脚本正文的键叫 source. 改名就是让卡片静默少一块内容, 见上一条用例的注释."""
    view = ApprovalView(
        plan=tool_plan(raw_command="bash clean.sh"),
        action_summary="执行 shell 命令",
        script_snapshots=(
            ScriptSnapshot(
                language="bash",
                origin="inline",
                path="/tmp/clean.sh",
                source="rm -rf dist\n",
            ),
        ),
    )
    snapshots = view.to_payload()["script_snapshots"]
    assert isinstance(snapshots, list)
    assert snapshots[0] == {
        "language": "bash",
        "origin": "inline",
        "path": "/tmp/clean.sh",
        "source": "rm -rf dist\n",
    }
