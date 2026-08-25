"""运行上下文: 让一条日志能被归到某一次会话, 某一轮, 某一次调用上.

没有它的时候, 排查一次真实故障是这样的: 日志里有 200 行 `tool.execute`, 你知道其中
一次卡住了, 但不知道是哪一次, 也不知道它属于哪一轮对话 —— 因为每一行都长得一样.

`RunContext` 用 contextvars 把这几个标识挂在**当前执行流**上, 由 `Log` 在写每一行时
自动拼进去. 调用点不必逐层把 turn_id 透传给每个下游函数, 下游也不必为了写日志而在
方法签名上多带一个参数.

选 contextvars 而不是 threading.local: Web 侧的 turn 跑在后台线程里, SSE 推送跑在
asyncio 任务里, 前者新线程起手是一份空 Context (于是拿到默认值, 不会串到别的项目),
后者每个 task 自带 Context 副本 (于是并发的两条流不会互相覆盖). threading.local 在
asyncio 下这两件事都做不到.

字段是**封闭**的六个: 它们是全局唯一能把日志接起来的那几个 id. 一次性的业务字段
(文件路径, 退出码, 命中了哪条规则) 直接写在那一行的 `**fields` 里, 不进这里 ——
放进来就意味着它要跟着整个调用栈跑, 而它只对一行有意义.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Final

__all__ = ["RunContext", "bind", "current", "reset", "update"]


@dataclass(frozen=True, slots=True)
class RunContext:
    """当前执行流所属的会话 / 轮次 / 调用. 空串与 0 表示"还不知道"."""

    session_id: str = ""
    turn_id: str = ""
    step: int = 0
    request_id: str = ""
    invocation_id: str = ""
    tool: str = ""

    def fields(self) -> dict[str, object]:
        """已知的标识, 供日志行拼接. 未知的字段不出现 —— 一行 `turn=` 是噪音."""
        known: dict[str, object] = {}
        if self.session_id:
            known["session"] = self.session_id
        if self.turn_id:
            known["turn"] = self.turn_id
        if self.step:
            known["step"] = self.step
        if self.request_id:
            known["req"] = self.request_id
        if self.invocation_id:
            known["inv"] = self.invocation_id
        if self.tool:
            known["tool"] = self.tool
        return known


# 什么都还不知道时的上下文. 它是 frozen dataclass, 共享一个实例没有风险.
_EMPTY: Final = RunContext()

_CURRENT: ContextVar[RunContext] = ContextVar("forge_run_context", default=_EMPTY)


def current() -> RunContext:
    """当前执行流的运行上下文."""
    return _CURRENT.get()


@contextmanager
def bind(
    *,
    session_id: str | None = None,
    turn_id: str | None = None,
    step: int | None = None,
    request_id: str | None = None,
    invocation_id: str | None = None,
    tool: str | None = None,
) -> Iterator[RunContext]:
    """在 with 块内追加标识; 退出时精确还原.

    传 None 的字段保持外层的值 —— 嵌套 bind 是**累加**而不是覆盖: 工具调用那一层只
    知道 invocation_id, turn_id 得由外面的 turn 那一层继续提供.

    用 ContextVar.reset(token) 而不是"记下旧值再 set 回去": 后者在同一个 Context 里
    并发进出时会把别人的值写回来.
    """
    base = _CURRENT.get()
    updated = RunContext(
        session_id=base.session_id if session_id is None else session_id,
        turn_id=base.turn_id if turn_id is None else turn_id,
        step=base.step if step is None else step,
        request_id=base.request_id if request_id is None else request_id,
        invocation_id=(base.invocation_id if invocation_id is None else invocation_id),
        tool=base.tool if tool is None else tool,
    )
    token = _CURRENT.set(updated)
    try:
        yield updated
    finally:
        _CURRENT.reset(token)


def update(
    *,
    session_id: str | None = None,
    turn_id: str | None = None,
    step: int | None = None,
    request_id: str | None = None,
    invocation_id: str | None = None,
    tool: str | None = None,
) -> None:
    """就地追加标识, 没有配套的还原点.

    给"没有一段 with 块能罩住它"的推进式流程用: AgentLoop 的步号与 request_id 在
    `_advance` 里往前走一格, 而这一格的作用范围一直延伸到下一格, 没有边界可言.

    之所以敢不还原: 它一定跑在外层某个 `bind` 里 (turn 那一层), 而 `bind` 退出时
    `ContextVar.reset(token)` 恢复的是**进入那个 with 之前**的值 —— 中间做过多少次
    `update` 都会被一起抹掉. 换句话说, 泄漏范围最多到外层 bind 的边界为止.
    """
    base = _CURRENT.get()
    _CURRENT.set(
        RunContext(
            session_id=base.session_id if session_id is None else session_id,
            turn_id=base.turn_id if turn_id is None else turn_id,
            step=base.step if step is None else step,
            request_id=base.request_id if request_id is None else request_id,
            invocation_id=(
                base.invocation_id if invocation_id is None else invocation_id
            ),
            tool=base.tool if tool is None else tool,
        )
    )


def reset() -> None:
    """把当前执行流的运行上下文清空.

    与 `update` 配套: 后者没有还原点, 所以"这一段结束了, 后面的日志不该再挂着它的
    标识"需要一个显式的清零动作. 测试用它隔离用例.
    """
    _CURRENT.set(_EMPTY)
