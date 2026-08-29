"""会话输入的两个领域枚举: 这一行是谁给的, 当前是哪一档模式。

原先这里还住着一组 ``UserIntent`` 值对象 (UserMessage / SlashCommand /
ManualShellIntent / UnknownCommand). 它们的唯一解析方 IntentRouter 与唯一执行方
斜杠命令注册表都在终端入口里, 随 ADR-0025 决策 1 的二次修订一起删除 —— Web 控制面
不解析文本意图: 消息就是消息, 配置项各有各的接口。

设计约束: 不可变, 值相等, 无副作用; 取值会落盘 (mode 进 state.json, origin 进
user_message 事件), 因此不可随意改写。
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "InputOrigin",
    "SessionMode",
]


class InputOrigin(Enum):
    """这一行输入是谁给的。

    人类说的话与程序合成的话必须分得开: 计划评审的"同意并执行"会以本轮的名义再入一次
    turn, 那句话是**程序**给的 (plan_review), 不能算用户又说了一遍。

    默认值是 PROGRAM: 新增一个调用方时, 忘记传参的后果是少算一次人类输入, 不是多算。

    早先还有一档 ``TTY_USER``, 承载人工 Shell 的特权 (ADR-0017 §2)。人工 Shell 随终端
    入口一起删除之后, 没有任何代码路径能再产出它, 所以它也不该继续留在这张表里 —— 一个
    永远取不到的权限档只会让读的人以为它还在用。老 events.jsonl 里可能留着
    ``"tty_user"`` 这个字符串, 但没有任何地方把它解析回枚举。
    """

    # 经本地 Web 控制面的认证用户。
    WEB_USER = "web_user"
    PROGRAM = "program"


class SessionMode(Enum):
    """会话模式 (ADR-0009 决策 6 的四档预设; 取值会落盘进 state.json, 不可改)."""

    ACCEPT_EDITS = "accept_edits"
    PLAN = "plan"
    AUTO = "auto"
    FULL_ACCESS = "full_access"

    def step(self, delta: int) -> SessionMode:
        """沿权限梯度移动 delta 档, 两端截断不回绕."""
        idx = _MODE_LADDER.index(self) + delta
        return _MODE_LADDER[max(0, min(idx, len(_MODE_LADDER) - 1))]


# 权限从紧到松, tab / shift+tab 按此序循环. 不用枚举声明顺序: 安全相关的次序应显式写出.
# ADR-0009 决策 13: 模式定义是代码级常量, 配置只能收紧, 不能放宽.
_MODE_LADDER: tuple[SessionMode, ...] = (
    SessionMode.PLAN,
    SessionMode.ACCEPT_EDITS,
    SessionMode.AUTO,
    SessionMode.FULL_ACCESS,
)
