"""审批服务: 把 ASK 变成一个阻塞的人类决定 (ADR-0013 §4, ADR-0043 决策 3).

ASK 是阻塞状态, 不是"返回错误让主模型自己决定要不要重试". 主模型不能代替用户确认: 它只
能放弃, 重写请求, 或主动把请求升级为 ASK.

**等待本身不在这里.** 挂起调用者, 摆卡片, 等人点, 取消时放开 —— 那一套由
``HumanPromptService`` 承担, 审批与 ``ask_user`` 共用同一条队列. 本模块只做两件审批
独有的事: 把一份 ``ApprovalView`` 摆成一条提示, 以及把回来的 ``choice`` 翻回
``ApprovalOutcome`` 与 ``ApprovalScope``.

这条统一之所以安全, 是因为**语义闸门本来就不在 broker 里**: 协调器在 ``request`` 返回
之后立刻调 ``ApprovalRequest.response_error``, 由它断言这个回答属于当前请求, ``scope``
确实在 ``view.allowed_scopes`` 里, 且 mandatory 时只能是 ``ONCE``. 底下换成什么队列,
这道闸都在原地.
"""

from __future__ import annotations

from forgecli.application.human_interaction.service import (
    HumanPromptService,
    PendingHumanPromptService,
)
from forgecli.domain.human_interaction.prompt import (
    HumanPrompt,
    PromptAnswer,
    PromptChoice,
    PromptKind,
)
from forgecli.domain.security.approval import (
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalResponse,
)
from forgecli.domain.security.vocabulary import ApprovalScope

__all__ = ["ApprovalService"]

# 拒绝那一项的稳定标识. 它不是一档 ApprovalScope —— 范围只在批准时才有意义.
_DENY = "deny"

# 给人读的那一行. 放在这里而不是各界面自己写: 终端与 Web 必须用同一套说法, 否则同一个
# 选项在两条入口上叫两个名字, 而两边都不会报错.
_SCOPE_LABELS: dict[ApprovalScope, str] = {
    ApprovalScope.ONCE: "允许这一次",
    ApprovalScope.WORKSPACE: "本工作区始终允许",
}


class ApprovalService:
    """人类审批入口: 把审批摆到统一的人机提示通道上.

    **不是抽象** (ADR-0028 规则 A). 早先它是个 ABC, 因为实现有两个 —— 一个阻塞等人的
    broker 住在 ``interfaces/``, 一个"无人可答"的空实现住在这里, 而前者构成依赖倒置.
    ADR-0043 把等待下沉到 ``HumanPromptService`` 之后, 这两条差异全都搬到了那一层:
    倒置在那里, 多实现也在那里. 这一层只剩一份逻辑 —— 怎么把审批摆成一条提示, 以及
    怎么把回答翻回审批语义 —— 而一个只有一份逻辑的抽象只会让每次改动都要动三处.

    默认接 ``PendingHumanPromptService``: 没有装交互界面时每个请求都停在 PENDING, 绝不
    自动放行. 非交互环境 (CI, 管道输入) 用的也是它.
    """

    def __init__(self, prompts: HumanPromptService | None = None) -> None:
        self._prompts = prompts or PendingHumanPromptService()

    def request(self, approval: ApprovalRequest) -> ApprovalResponse:
        """阻塞等待人类决定. 没拿到决定时返回 PENDING, 绝不返回 APPROVED."""
        answer = self._prompts.ask(_prompt_of(approval))
        return _response_of(approval, answer)


def _prompt_of(approval: ApprovalRequest) -> HumanPrompt:
    """把一次待决议的调用摆成一条提示.

    ``detail`` 直接装 ``ApprovalView.to_payload()``, **不另写一份字段搬运** (ADR-0043
    决策 3 纪律 2): 用户批准的对象是被 ``view_hash`` 绑定的那份视图, 而多一份手工维护的
    投影就多一个"显示的和绑定的不是同一件事"的位置.

    唯一补上的键是 ``mandatory`` —— 它是 ``ApprovalRequest`` 上的事实, 不在视图里, 而
    卡片要靠它说明"这次没有始终允许"不是界面漏了一个选项 (ADR-0013 §4.1).
    """
    view = approval.view
    choices = [
        PromptChoice(scope.value, _SCOPE_LABELS[scope])
        for scope in view.allowed_scopes
        if scope in _SCOPE_LABELS
    ]
    # 拒绝永远排在最后, 而且永远在: 一条只能批准的审批不是审批.
    choices.append(PromptChoice(_DENY, "拒绝"))
    return HumanPrompt(
        prompt_id=approval.approval_id,
        kind=PromptKind.APPROVAL,
        title=f"需要你确认: {view.plan.tool_name}",
        body=view.raw_command,
        choices=tuple(choices),
        # 授权的取值集合必须封闭: 一段自由文本翻不成任何一档 ApprovalScope, 收下它只会
        # 让用户以为自己附加的条件生效了.
        free_text=False,
        detail={**view.to_payload(), "mandatory": approval.mandatory},
    )


def _response_of(approval: ApprovalRequest, answer: PromptAnswer) -> ApprovalResponse:
    """把一个哑回答翻回审批语义.

    这是整条链上唯一一处 ``choice`` -> ``ApprovalScope`` 的映射, 而它的产出还要过
    ``ApprovalRequest.response_error``. 任何认不出的取值一律落到 PENDING —— 失效方向朝
    "没批准", 不朝"批准了".
    """
    if not answer.resolved:
        return ApprovalResponse(
            outcome=ApprovalOutcome.PENDING,
            approval_id=approval.approval_id,
            note=answer.note or "审批未完成",
        )
    if answer.choice == _DENY:
        return ApprovalResponse(
            outcome=ApprovalOutcome.DENIED,
            approval_id=approval.approval_id,
            note="用户拒绝",
        )
    try:
        scope = ApprovalScope(answer.choice)
    except ValueError:
        return ApprovalResponse(
            outcome=ApprovalOutcome.PENDING,
            approval_id=approval.approval_id,
            note=f"认不出的决议: {answer.choice}",
        )
    return ApprovalResponse(
        outcome=ApprovalOutcome.APPROVED,
        approval_id=approval.approval_id,
        scope=scope,
        note="用户批准",
    )
