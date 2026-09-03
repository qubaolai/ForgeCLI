"""分隔行与它的转义 (ADR-0018 §5.3).

上下文里有两段是用**整行分隔符**围起来的: 项目指令 (`FORGE.md`) 与当前状态帧. 两段的
共同点是它们的正文**不是 Forge 写的** —— 一段是用户写的, 一段是模型自己记下的 —— 而围栏
的全部作用就是让模型分得清"这一段是谁说的".

不转义的话, 正文里只要自己写一行与结束标记同形的字, 后面的内容看起来就跑到了受信任区段
外面. 项目指令那边是提权 (让 FORGE.md 的内容看起来像 Forge 自己的规则); 状态帧这边方向
相反但一样糟: 让模型记下的一条东西看起来像用户这一轮说的话.

分隔行是 ASCII 结构标记, 不是措辞, 所以它们与转义器同住 —— 转义要认的那一行必须与写出去
的那一行逐字节相同, 分两处放迟早对不上.
"""

from __future__ import annotations

from forgecli.application.prompt.template_renderer import render_notice

__all__ = [
    "STATE_BEGIN",
    "STATE_END",
    "WORKSPACE_BEGIN",
    "WORKSPACE_END",
    "escape_sentinels",
]

WORKSPACE_BEGIN = "--- BEGIN WORKSPACE INSTRUCTIONS ---"
WORKSPACE_END = "--- END WORKSPACE INSTRUCTIONS ---"
STATE_BEGIN = "--- BEGIN CURRENT STATE ---"
STATE_END = "--- END CURRENT STATE ---"


def escape_sentinels(text: str, *sentinels: str) -> str:
    """把正文里与分隔行同形的整行转义掉.

    按**整行**比对而不是子串: 一句正常的话里提到 `--- END CURRENT STATE ---` 这串字符
    并不构成越界, 只有它独占一行时才会被读成结构标记.
    """
    marks = {mark.strip() for mark in sentinels}
    return "\n".join(
        render_notice("prompt.instruction_escaped_line", line=line)
        if line.strip() in marks
        else line
        for line in text.split("\n")
    )
