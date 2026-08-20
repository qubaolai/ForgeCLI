"""进运行事件之前对文本做的唯一处理: 剥控制字符.

只有这一步, 而且两个发布点必须共用同一份 —— 循环发 TOOL_QUEUED 时带原始入参, 协调器
发 TOOL_PREPARED 时带归一化入参, 两边如果各写各的清理, 同一个参数在页面上会有两种样子,
而"哪一份是真的"就成了排查时先要回答的问题.

不做脱敏, 也不留 allowlist 之类的开关: 工具入参是用户判断"要不要让这次调用发生"的依据,
挡掉一部分只会让他在看不全的信息上做决定. 控制字符仍然要剥 —— 那不是隐藏内容, 是防止
参数里的 ANSI 序列重画终端.

**换行与制表符留着.** 它们不能重画屏幕, 而 fs.create_file 的 content 本来就是多行代码:
把 `\n` 删掉, 一段函数体会拼成 `def hi():    return 1` —— 看起来像一行合法代码, 实际
上是把结构悄悄抹掉了, 用户据此批准的东西和真正要写的东西不是一回事. 展示端各自负责
排版: 终端把换行转义成可见的 `\n` 再限长, 网页按行显示.
"""

from __future__ import annotations

from collections.abc import Mapping

__all__ = ["scrub_arguments", "scrub_text"]


_KEPT_CONTROLS = frozenset({"\n", "\t"})


def scrub_text(text: str) -> str:
    return "".join(
        char
        for char in text
        if char in _KEPT_CONTROLS or (char >= " " and char != "\x7f")
    )


def scrub_arguments(arguments: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    """入参逐字进事件, 按名字排序.

    排序是为了两次相同的调用在页面上长得一样 —— 顺序随 dict 插入次序变化的话, 用户
    很难看出两次调用到底哪里不同.
    """
    return tuple(
        (name, scrub_text(str(value))) for name, value in sorted(arguments.items())
    )
