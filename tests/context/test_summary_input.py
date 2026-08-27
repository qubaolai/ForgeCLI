"""送进二级摘要的那份拍平文本 (ADR-0032 决策 2).

摘要模型只看得到这一份. 它读不到的东西, 就是压缩之后彻底消失的东西 —— 而压缩指令
点名要它保住"改过或正在关注的文件".
"""

from __future__ import annotations

from forgecli.application.context.summarize import _flatten
from forgecli.domain.conversation.message import ChatMessage, TextBlock
from forgecli.domain.conversation.turn import MessageRole
from forgecli.domain.tool.tool_call import ToolCall

_PATCH = (
    "*** NEW backend/src/main/java/com/example/demo/security/JwtTokenProvider.java\n"
    + "code line\n" * 200
)


def _writing() -> ChatMessage:
    return ChatMessage(
        role=MessageRole.ASSISTANT,
        content=(),
        tool_calls=(
            ToolCall(
                tool_call_id="c1", name="fs_apply_patch", arguments={"patch": _PATCH}
            ),
        ),
    )


def test_the_summary_input_says_which_file_was_touched() -> None:
    """只留工具名的话, 摘要模型看到的是 `-> fs_apply_patch`, 读不出动了哪个文件."""
    flattened = _flatten((_writing(),))

    assert "fs_apply_patch" in flattened
    assert "JwtTokenProvider.java" in flattened


def test_the_summary_input_does_not_carry_the_patch_body() -> None:
    """补丁正文是 transcript 里最大的一块.

    原样送进摘要请求, 等于把正要压缩掉的内容再完整发一遍 —— 而这次调用发生在上下文
    已经吃紧的时刻.
    """
    flattened = _flatten((_writing(),))

    assert len(flattened) < len(_PATCH) // 4
    assert flattened.count("code line") <= 6


def test_text_and_tool_calls_both_survive_flattening() -> None:
    message = ChatMessage(
        role=MessageRole.ASSISTANT,
        content=(TextBlock("先建 JWT 工具类"),),
        tool_calls=_writing().tool_calls,
    )

    flattened = _flatten((message,))

    assert "先建 JWT 工具类" in flattened
    assert "fs_apply_patch" in flattened
