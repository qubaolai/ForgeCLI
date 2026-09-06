"""人机提示: 挂起一个调用者, 等一个人回一句话 (ADR-0043 决策 3).

Forge 里有两种"问人": 安全策略判出 ASK 时问"这次调用许不许可", 模型缺业务信息时问
"你想要哪一种". 两者的**语义毫无共同之处** —— 前者的回答会经重验签出
``ExecutionAuthorization``, 后者的回答只是一段文本 —— 但**等待的形状完全一样**: 挂起
调用者, 在界面上摆一张卡片, 等一个人点一下, 把结果交回去, 并在取消或退出时放开所有
还在等的.

这个模块就是那个形状, 而且**只是那个形状**.

**``HumanPrompt`` 必须保持哑** (ADR-0043 决策 3 纪律 1). 它里面不许出现
``ApprovalScope``, ``ToolPlan`` 或任何安全词汇: 一旦出现, 下一次给审批加字段就会顺手
让提问也拿到它, 而那个字段可能正是一档授权范围. ``scripts/check_arch.py`` 有一条断言
守着这件事, 不靠自觉.

于是"回答意味着什么"一律在上层的适配器里决定: ``ApprovalService`` 把
``choice`` 映射回 ``ApprovalOutcome`` 与 ``ApprovalScope``, ``AskUserTool`` 把 ``text``
原样当成工具结果. 两者互不 import.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum

__all__ = ["HumanPrompt", "PromptAnswer", "PromptChoice", "PromptKind"]


class PromptKind(Enum):
    """这条提示是哪一类. **只用于选渲染分支与审计, 不用于分派语义.**

    通道不按它决定回答意味着什么 —— 那由各自的适配器决定. 它在这里的唯一职责是让一张
    卡片知道自己该长成审批的样子还是提问的样子, 以及让每轮提问上限认得出该数哪一种
    (ADR-0043 决策 9).
    """

    APPROVAL = "approval"
    QUESTION = "question"


@dataclass(frozen=True)
class PromptChoice:
    """一个可点的选项.

    ``value`` 是回传给适配器的稳定标识 (审批那侧是 ``once`` / ``workspace`` / ``deny``),
    ``label`` 是给人读的那一行, ``detail`` 是可选的补充说明.
    """

    value: str
    label: str
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("PromptChoice.value 不能为空")
        if not self.label.strip():
            raise ValueError("PromptChoice.label 不能为空")

    def to_payload(self) -> dict[str, str]:
        return {"value": self.value, "label": self.label, "detail": self.detail}


@dataclass(frozen=True)
class HumanPrompt:
    """一条待人作答的提示.

    ``detail`` 是**不透明的**: 通道原样搬运, 不解释其中任何一个键. 审批那侧装的就是
    ``ApprovalView.to_payload()`` 的返回值 —— 用户批准的对象是被 ``view_hash`` 绑定的
    那份视图, 而多一份手工维护的投影就多一个"显示的和绑定的不是同一件事"的位置
    (ADR-0043 决策 3 纪律 2).
    """

    prompt_id: str
    kind: PromptKind
    title: str
    body: str = ""
    choices: tuple[PromptChoice, ...] = ()
    # 除了选项之外还收不收自由文本. 提问恒为 True (ADR-0043 决策 7: 选项是建议,
    # 自由文本永远可答), 审批恒为 False —— 一次授权的取值集合必须是封闭的.
    free_text: bool = False
    detail: Mapping[str, object] = field(default_factory=dict)
    selection_mode: str = "single"
    recommended_option_id: str = ""
    allow_skip: bool = False

    def __post_init__(self) -> None:
        if not self.prompt_id.strip():
            raise ValueError("HumanPrompt.prompt_id 不能为空")
        if not self.title.strip():
            raise ValueError("HumanPrompt.title 不能为空")
        if not self.choices and not self.free_text:
            # 一条既没有选项又不收文字的提示, 摆出来之后没有任何作答方式, 而它会永远
            # 挂在队列里把调用者一起挂住.
            raise ValueError("HumanPrompt 必须至少有一种作答方式")
        values = [choice.value for choice in self.choices]
        if len(values) != len(set(values)):
            raise ValueError("HumanPrompt.choices 的 value 不能重复")
        if self.selection_mode not in {"single", "multiple"}:
            raise ValueError("selection_mode 必须是 single 或 multiple")
        if self.recommended_option_id and self.recommended_option_id not in values:
            raise ValueError("推荐项必须来自选项")
        if self.kind is PromptKind.APPROVAL and (
            self.selection_mode != "single" or self.allow_skip
        ):
            raise ValueError("审批只支持单选且不能跳过")

    def accepts_answer(self, answer: PromptAnswer) -> bool:
        # 审批协议继续使用单个 choice，不能用问题的回答数组替代。
        if self.kind is PromptKind.APPROVAL and answer.selected_values:
            return False
        if answer.skipped:
            return self.allow_skip and not (
                answer.choice or answer.selected_values or answer.text
            )
        if answer.choice and answer.selected_values:
            return False
        values = answer.selected_values or ((answer.choice,) if answer.choice else ())
        if len(values) != len(set(values)):
            return False
        if self.selection_mode == "single" and len(values) > 1:
            return False
        if any(not value or not self.accepts(value) for value in values):
            return False
        if answer.text and not self.free_text:
            return False
        return bool(values) or (self.free_text and bool(answer.text.strip()))

    def accepts(self, choice: str) -> bool:
        """这个 ``choice`` 是不是本条提示提供过的选项.

        空串表示"没选任何一项", 只有在收自由文本时才成立. 通道用它做**唯一**的一道
        校验 —— 它答得出"这一项是我摆出来的吗", 答不出"这一项意味着什么", 而后者本来
        也不该由通道回答.
        """
        if not choice:
            return self.free_text
        return any(item.value == choice for item in self.choices)

    def to_payload(self) -> dict[str, object]:
        """交给界面的 JSON 形状. 两个界面 (Web 与终端) 读的是同一份."""
        return {
            "prompt_id": self.prompt_id,
            "kind": self.kind.value,
            "title": self.title,
            "body": self.body,
            "choices": [choice.to_payload() for choice in self.choices],
            "free_text": self.free_text,
            "detail": dict(self.detail),
            "selection_mode": self.selection_mode,
            "recommended_option_id": self.recommended_option_id,
            "allow_skip": self.allow_skip,
        }


@dataclass(frozen=True)
class PromptAnswer:
    """一个人对一条提示的回答, 或者"没有人回答".

    ``resolved`` 表示人已提交，``skipped`` 明确表示跳过；空白不是有效回答。
    ``selected_values`` 保存单选/多选，``choice`` 兼容审批及旧调用者。
    """

    prompt_id: str
    choice: str = ""
    text: str = ""
    resolved: bool = False
    # 没人回答时说明为什么 (取消了 / 进程在退出 / 这个环境里没有人). 进审计与回给模型
    # 的结果, 不进任何裁决.
    note: str = ""
    selected_values: tuple[str, ...] = ()
    skipped: bool = False
