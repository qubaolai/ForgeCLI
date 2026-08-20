"""用户意图相关领域概念。

本模块只描述 intent 的领域语义，不耦合 Typer 参数、Rich 输出或 REPL 输入格式。
设计约束：
    - 不可变：全部使用 frozen dataclass。
    - 值相等：dataclass 自动按字段比较，不同子类型互不相等。
    - 构造即有效：非法组合尽量在类型层面不可表达，剩余不变量在 __post_init__ 校验。
    - 无副作用：只承载数据与纯查询，不做解析，不对接 LLM，不执行工具。

解析职责属于 IntentRouter：它负责把原始输入识别为下面的具体子类型。
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "InputOrigin",
    "ManualShellIntent",
    "SessionMode",
    "UserIntent",
    "UserMessage",
    "SlashCommand",
    "UnknownCommand",
]


class InputOrigin(Enum):
    """这一行输入是谁给的 (ADR-0017 §2).

    人工 Shell 的特权来自**输入来源**, 而不是 `#` 这个字符本身. 只有当前前台 TTY 输入
    适配器可以标 TTY_USER; 模型文本, 工具输出, 事件重放, resume 与任何自动化入口都只能
    是 PROGRAM —— 于是"让模型说一句 # 就拿到不受裁决的 Shell"这条路在类型层面就不存在.

    默认值是 PROGRAM 而不是 TTY_USER: 新增一个调用方时, 忘记传参的后果是少一项特权,
    不是多一项.
    """

    TTY_USER = "tty_user"
    # 经本地 Web 控制面的认证用户。它是人类输入，但没有 TTY 人工 Shell 特权。
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


@dataclass(frozen=True)
class UserIntent(ABC):
    """所有用户意图的值对象基类。

    不直接实例化本类，应使用下方具体子类型。所有意图都携带 raw_text，
    以保留用户原始输入，供回显、日志和测试使用。
    """

    raw_text: str

    def __post_init__(self) -> None:
        if not self.raw_text or not self.raw_text.strip():
            raise ValueError("raw_text 不能为空")


@dataclass(frozen=True)
class UserMessage(UserIntent):
    """自然语言输入，原样交给后续对话流程。"""

    @property
    def text(self) -> str:
        return self.raw_text


@dataclass(frozen=True)
class SlashCommand(UserIntent):
    """已识别的斜杠命令，例如 /help、/status、/plan。

    command 为去掉前导斜杠的规范名（小写），args 为不可变参数序列。
    命令语义由 application 层注册的 handler 执行，领域层只承载解析结果。
    """

    command: str
    args: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.command or not self.command.strip():
            raise ValueError("command 不能为空")


@dataclass(frozen=True)
class ManualShellIntent(UserIntent):
    """用户要求把终端交给自己的 Shell (ADR-0017).

    两种形态共用一个意图, 因为它们的**信任语义完全相同** —— 都是当前 TTY 用户亲手敲的,
    都不经过 mode, HITL 与工具管线:

    - ``command`` 为空 (`#`): 开一次完整交互式会话.
    - ``command`` 非空 (`# clear`): 跑一条就回来.

    构造时强制 origin 必须是 TTY_USER. 这条校验放在值对象里而不是路由里, 是为了让
    "从别的地方伪造一个人工 Shell 意图"连对象都构造不出来 —— 路由是可以绕过的, 构造
    函数不行.
    """

    origin: InputOrigin = InputOrigin.TTY_USER
    command: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.origin is not InputOrigin.TTY_USER:
            raise ValueError("人工 Shell 只能由前台 TTY 用户输入触发")

    @property
    def interactive(self) -> bool:
        return not self.command


@dataclass(frozen=True)
class UnknownCommand(UserIntent):
    """无法识别的斜杠命令，必须携带可操作的错误信息（提示 /help）。"""

    command: str
    error_message: str

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.command or not self.command.strip():
            raise ValueError("command 不能为空")
        if not self.error_message or not self.error_message.strip():
            raise ValueError("error_message 不能为空")
