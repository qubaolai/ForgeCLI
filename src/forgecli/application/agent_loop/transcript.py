"""把工具调用与消息渲染成模型读得懂的文字 (ADR-0010 §10).

全是纯函数, 没有循环状态 —— 从 ``builtin_loop`` 分出来是因为它们回答的是另一个问题:
控制流关心"下一步做什么", 这里关心"这一步怎么写进 transcript 才不会被模型读错".

两条都不是格式偏好, 各自挡着一类真实故障, 见每个函数自己的说明.
"""

from __future__ import annotations

from forgecli.domain.conversation.message import (
    ChatMessage,
    TextBlock,
    ToolResultBlock,
)
from forgecli.domain.tool.tool_call import ToolCall

__all__ = ["labelled", "protocol_markup_in", "render_call", "transcript_view"]

# 模型侧工具调用 markup. 它出现在**参数值或工具名**里, 说明供应商没能把模型的工具调用
# 解析干净, 把标记连同内容一起塞进了参数.
#
# 这类调用不能当正常参数派发下去: 它会一路走到安全裁决, 拿回 executable_not_found 之类
# 的结论, 而那个结论会把模型引向"我命令写错了" —— 真正坏掉的是它的输出格式, 照着错误
# 的结论改只会一直错下去.
_PROTOCOL_MARKUP = (
    "<tool_call>",
    "</tool_call>",
    "<arg_key>",
    "<arg_value>",
    "<think>",
    "</think>",
    "<tool_calls>" "</tool_calls>",
)


def protocol_markup_in(call: ToolCall) -> str | None:
    """调用里混进的第一个 markup 标记; 干净则返回 None.

    只看工具名与**字符串**参数值: 结构化的嵌套值不会承载这类泄漏, 而把整个参数字典
    序列化去搜会把正常的代码内容误判成 markup —— 模型完全可能在写一段含 `<think>`
    的 HTML.
    """
    for marker in _PROTOCOL_MARKUP:
        if marker in call.name:
            return marker
        for value in call.arguments.values():
            if isinstance(value, str) and marker in value:
                return marker
    return None


def labelled(call: ToolCall, content: str) -> str:
    """给回填内容加一行调用标签.

    协议层靠 tool_call_id 关联, 但模型是**读**上下文的. 一段几百行的裸文件列表和另一段
    长得一模一样, 模型认不出哪段对应哪次调用, 于是"再列一次看看" —— 这是重复调用最主要
    的来源. 标签让每段结果自带身份.
    """
    return f"{render_call(call)} ->\n{content}"


def render_call(call: ToolCall) -> str:
    arguments = ", ".join(
        f"{name}={value!r}" for name, value in sorted(call.arguments.items())
    )
    return f"{call.name}({arguments})"


def transcript_view(messages: tuple[ChatMessage, ...]) -> list[dict[str, object]]:
    """把 transcript 摊平成可以直接读的形状, 供 debug 级日志写出整段上下文.

    排查"模型为什么突然这么答"时, 真正要看的就是那一刻发给它的完整消息序列 —— 事件
    日志里只有落盘的成对 user / assistant, 轮内的 tool result 回填与压缩改写不在那里.
    只在 DEBUG 下调用, info 级别不会为它拼这个字符串.
    """
    view: list[dict[str, object]] = []
    for message in messages:
        item: dict[str, object] = {"role": message.role.value}
        texts = [
            block.text for block in message.content if isinstance(block, TextBlock)
        ]
        if texts:
            item["text"] = "\n".join(texts)
        results = [
            {
                "tool_call_id": block.tool_call_id,
                "is_error": block.is_error,
                "content": block.content,
            }
            for block in message.content
            if isinstance(block, ToolResultBlock)
        ]
        if results:
            item["tool_results"] = results
        if message.tool_calls:
            item["tool_calls"] = [
                {"id": call.tool_call_id, "name": call.name, "args": call.arguments}
                for call in message.tool_calls
            ]
        view.append(item)
    return view
