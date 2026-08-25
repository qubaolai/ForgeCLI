"""在消息序列上定位与改写工具结果 (ADR-0032).

去重, 降级与摘要三条通路都要做同一件事: 找到 transcript 里的工具结果, 换掉它的正文.
这里只放**机制** —— 找哪些, 换成什么由各自的策略模块决定.

改写走"算出一份替换表, 再整体重建元组", 不是原地逐条改: ``ChatMessage`` 与
``ToolResultBlock`` 都是 frozen 的, 而且分两步之后策略模块拿到的是完整视图, 不必一边
遍历一边考虑自己刚改过的那一条会不会影响后面的判断.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from forgecli.domain.conversation.message import ChatMessage, ToolResultBlock
from forgecli.domain.conversation.turn import MessageRole

__all__ = ["Slot", "rewrite", "safe_split_points", "slots_of"]


@dataclass(frozen=True)
class Slot:
    """transcript 里一条工具结果的位置.

    ``ordinal`` 是"本轮第几次工具调用", 从 1 起 —— 占位文本里的 ``{index}`` 就是它.
    用序号而不是 ``tool_call_id``: 前者模型往回数就能对上, 后者是一串它读不出顺序的
    随机字符.
    """

    message_index: int
    block_index: int
    ordinal: int
    block: ToolResultBlock

    @property
    def key(self) -> tuple[int, int]:
        return (self.message_index, self.block_index)


def slots_of(messages: tuple[ChatMessage, ...]) -> tuple[Slot, ...]:
    """按出现顺序列出全部工具结果块."""
    found: list[Slot] = []
    for message_index, message in enumerate(messages):
        if message.role is not MessageRole.TOOL:
            continue
        for block_index, block in enumerate(message.content):
            if not isinstance(block, ToolResultBlock):
                continue
            found.append(
                Slot(
                    message_index=message_index,
                    block_index=block_index,
                    ordinal=len(found) + 1,
                    block=block,
                )
            )
    return tuple(found)


def rewrite(
    messages: tuple[ChatMessage, ...], replacements: Mapping[tuple[int, int], str]
) -> tuple[ChatMessage, ...]:
    """把指定位置的工具结果正文换掉, 其余原样返回.

    ``provenance`` 跟着留下. 换掉的是模型看到的那段字, 不是"这条结果是什么的快照"
    这个事实 —— 丢了它, 下一次 fit 就认不出这条已经降级过, 会把占位文本再降一遍.
    """
    if not replacements:
        return messages
    out: list[ChatMessage] = []
    for message_index, message in enumerate(messages):
        blocks = list(message.content)
        touched = False
        for block_index, block in enumerate(blocks):
            text = replacements.get((message_index, block_index))
            if text is None or not isinstance(block, ToolResultBlock):
                continue
            blocks[block_index] = replace(block, content=text)
            touched = True
        out.append(replace(message, content=tuple(blocks)) if touched else message)
    return tuple(out)


def safe_split_points(messages: tuple[ChatMessage, ...]) -> tuple[int, ...]:
    """可以在此处一刀两断而不破坏 tool call 配对的位置.

    这是二级摘要唯一必须守住的不变量: 带 ``tool_calls`` 的 assistant 消息后面欠着一份
    配对的 tool result, 从中间切开, 供应商会直接拒掉整个请求 (ADR-0011 §10). 而这类
    失败发生在压缩之后, 也就是上下文已经超长的时候 —— 那时再失败一次, 这一轮就彻底
    没救了.

    判据是"到这个位置为止, 没有还欠着的 tool result".
    """
    points: list[int] = []
    owed: set[str] = set()
    for index, message in enumerate(messages):
        if not owed:
            points.append(index)
        for call in message.tool_calls:
            owed.add(call.tool_call_id)
        for block in message.content:
            if isinstance(block, ToolResultBlock):
                owed.discard(block.tool_call_id)
    if not owed:
        points.append(len(messages))
    return tuple(points)
