"""调用前 token 估算 (ADR-0011 §11.4).

这份估算是压缩的唯一触发依据. 少算的后果不是"压缩晚了一点", 是发出一个必然被供应商
拒掉的请求 —— 一整轮工作连同已经花掉的 token 一起作废, 而且 400 之后没有第二次机会.
"""

from __future__ import annotations

from forgecli.application.llm.gateway.token_estimator import ApproximateTokenEstimator
from forgecli.domain.conversation.message import ChatMessage, TextBlock
from forgecli.domain.conversation.turn import MessageRole
from forgecli.domain.tool.tool_call import ToolCall


def _assistant_writing(content: str) -> ChatMessage:
    return ChatMessage(
        role=MessageRole.ASSISTANT,
        content=(),
        tool_calls=(
            ToolCall(
                tool_call_id="c1",
                name="fs_apply_patch",
                arguments={"patch": content},
            ),
        ),
    )


def test_tool_call_arguments_count_toward_the_estimate() -> None:
    """模型写进补丁的整份文件曾经完全不计数.

    ``tool_calls`` 是 ChatMessage 上与 content 并列的另一个字段, 而估算只遍历 content.
    实测两轮真实 transcript, 漏掉的这部分占全部字符的 44% 与 45% —— 一个写代码的 Agent
    里最大的一块.
    """
    estimator = ApproximateTokenEstimator()
    message = _assistant_writing("x" * 20000)

    assert estimator.estimate_input(messages=(message,)) > 6000


def test_cjk_is_denser_than_latin() -> None:
    """同样的字符数, 中文比代码多得多的 token.

    系统提示词与工具描述整段是中文: 按拉丁字符的比例折算, 第一次请求就已经少算近一半.
    """
    estimator = ApproximateTokenEstimator()

    assert estimator.estimate_text("中" * 1000) > estimator.estimate_text("a" * 1000)


def test_a_transcript_estimate_covers_both_directions_of_a_turn() -> None:
    """模型写出去的和工具回来的都要算: 少算任何一侧都会低估整段 transcript."""
    estimator = ApproximateTokenEstimator()
    written = _assistant_writing("y" * 3000)
    read_back = ChatMessage(
        role=MessageRole.USER, content=(TextBlock(text="z" * 3000),)
    )

    both = estimator.estimate_input(messages=(written, read_back))

    assert both > estimator.estimate_input(messages=(written,))
    assert both > estimator.estimate_input(messages=(read_back,))
