"""人机提示通道: 所有阻塞式"问人"的唯一入口 (ADR-0043 决策 3).

安全审批与 ``ask_user`` 共用它. **共用的是等待, 不是语义** —— 通道交回一个
``PromptAnswer`` 就结束了, "这个回答意味着什么"由各自的适配器决定:
``ApprovalService`` 把 ``choice`` 映射回 ``ApprovalOutcome`` 与
要施加的建议, ``AskUserQuestionTool`` 把 ``text`` 原样当成工具结果.

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

from forgecli.human_interaction.prompt import HumanPrompt, PromptAnswer, PromptKind

__all__ = [
    "MAX_QUESTIONS_PER_TURN",
    "HumanPromptService",
    "PendingHumanPromptService",
]

# 一轮里模型最多能问几个问题.
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
