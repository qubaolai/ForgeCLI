"""人机提示通道: 所有阻塞式"问人"的唯一入口 (ADR-0043 决策 3).

安全审批与 ``ask_user`` 共用它. **共用的是等待, 不是语义** —— 通道交回一个
``PromptAnswer`` 就结束了, "这个回答意味着什么"由各自的适配器决定:
``ApprovalService`` 把 ``choice`` 映射回 ``ApprovalOutcome`` 与
``ApprovalScope``, ``AskUserTool`` 把 ``text`` 原样当成工具结果.

**不设等待超时.** 正确的终止条件只有三个: 人做出决定, 人停止这一轮, 进程退出. 挂钟到点
就把提示判成"没人答", 等于让用户去泡杯咖啡的功夫决定这次调用的命运 —— 而审批那侧收到
的会是"未获授权", 随后整轮停摆, 用户回来时既看不到卡片, 也没有任何补救入口. 三个真实
终止条件都由 ``release_pending`` 显式触发.

``CancelToken`` 顶替不了 ``release_pending``: 它是轮询式的 (见
``shared/cancellation.py``), 而一个阻塞在 ``threading.Event.wait()`` 上的线程永远不会
回头去看它.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from forgecli.domain.human_interaction.prompt import (
    HumanPrompt,
    PromptAnswer,
    PromptKind,
)

__all__ = [
    "MAX_QUESTIONS_PER_TURN",
    "HumanPromptService",
    "PendingHumanPromptService",
]

# 一轮里模型最多能问几个问题 (ADR-0043 决策 9).
#
# 管的是**人的注意力预算**, 不是模型的步数预算, 所以它在通道上而不是在循环里 —— 放进
# 循环则要求循环认识"这次调用是一次提问".
#
# **只对 QUESTION 生效, 审批不限流**: 审批超额之后唯一安全的处置是当成未批准, 那会把一
# 次正常操作变成失败; 提问超额的处置是"你自己判断", 那是无害的. 判据是超额的默认处置
# 伤不伤人, 不是"都是问人所以一视同仁".
#
# 3 是个钝的数字, 与 ADR-0023 的评审链上限同源, 同样区分不了"用户在认真澄清需求"和
# "模型在原地打转".
MAX_QUESTIONS_PER_TURN = 3


class HumanPromptService(ABC):
    """挂起调用者, 等一个人回一句话."""

    @abstractmethod
    def ask(self, prompt: HumanPrompt) -> PromptAnswer:
        """阻塞直到有人作答, 或者这条提示被放开.

        没人作答时返回 ``resolved=False``, **绝不**编一个回答出来: 审批那侧会把它读成
        "没批准", 而任何一个凭空产生的 ``choice`` 都可能被读成"批准了".
        """

    @abstractmethod
    def begin_turn(self) -> None:
        """一轮开始. 归零本轮的提问计数 (``MAX_QUESTIONS_PER_TURN``)."""

    @abstractmethod
    def cancel_turn(self, note: str) -> None:
        """本轮被取消: 放开还在等的提示, 并且不再接受这一轮的新提示.

        与 ``release_pending`` 的区别就是后半句, 而那半句正是"停止"按钮需要的
        (ADR-0048 决策 3): 只放开一次队列的话, 取消与入队交错时, 按钮已经返回成功,
        后台却刚刚新增了一个永远等不到人的等待者.

        取消在本轮内不可逆, 下一次 ``begin_turn`` 才解除.
        """

    @abstractmethod
    def release_pending(self, note: str) -> None:
        """放开所有还在等的提示, 按"没人回答"处理."""

    @abstractmethod
    def close(self) -> None:
        """进程退出: 放开一切, 之后不再接受新的提示."""


class PendingHumanPromptService(HumanPromptService):
    """无人可答时的实现: 一律 ``resolved=False``, 立即返回.

    用于非交互装配 (无头运行, 测试). 两侧适配器各自朝安全的方向翻译这个结果 —— 审批读成
    ``PENDING`` (没答案就是没批准), 提问读成"没人可问, 你自己判断"(ADR-0043 决策 8).

    **两个方向不一样, 而这是对的**: 审批那里没人回答意味着不能执行, 继续派工具没有意义;
    提问这里没人回答只意味着模型得自己拿主意, 而那本来就是它绝大多数时候在做的事.
    """

    def ask(self, prompt: HumanPrompt) -> PromptAnswer:
        return PromptAnswer(
            prompt_id=prompt.prompt_id,
            resolved=False,
            note=(
                "当前环境无法进行交互式审批"
                if prompt.kind is PromptKind.APPROVAL
                else "当前环境没有人可以回答"
            ),
        )

    def begin_turn(self) -> None:
        return None

    def cancel_turn(self, note: str) -> None:
        return None

    def release_pending(self, note: str) -> None:
        return None

    def close(self) -> None:
        return None
