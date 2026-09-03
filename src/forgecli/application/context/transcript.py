"""在消息序列上找一刀两断的位置 (ADR-0041 决策 5).

原先这里还有 `Slot` / `slots_of` / `rewrite` 三样, 供去重与降级按位置改写工具结果正文.
它们随 ADR-0041 一起删了: 窗口只追加, 而**留着机制入口就等于留着退回去的路** ——
"回头整理一下历史"永远是看起来合理的, 而在前缀缓存下它永远是负收益.

于是这个模块只剩一个函数.
"""

from __future__ import annotations

from forgecli.domain.conversation.message import ChatMessage, ToolResultBlock

__all__ = ["safe_split_points"]


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
