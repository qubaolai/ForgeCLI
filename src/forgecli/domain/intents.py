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

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum, auto

__all__ = [
    "IntentKind",
    "SessionMode",
    "UserIntent",
    "UserMessage",
    "SlashCommand",
    "UnknownCommand",
]


class IntentKind(Enum):
    """意图判别枚举，便于日志、测试断言与序列化。"""

    USER_MESSAGE = auto()
    SLASH_COMMAND = auto()
    UNKNOWN_COMMAND = auto()


class SessionMode(Enum):
    """会话模式"""

    ACCEPT_EDITS = "accept_edits"
    PLAN = "plan"
    AUTO = "auto"
    FULL_ACCESS = "full_access"



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

    @property
    @abstractmethod
    def kind(self) -> IntentKind: ...


@dataclass(frozen=True)
class UserMessage(UserIntent):
    """自然语言输入，原样交给后续对话流程。"""

    @property
    def kind(self) -> IntentKind:
        return IntentKind.USER_MESSAGE

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

    @property
    def kind(self) -> IntentKind:
        return IntentKind.SLASH_COMMAND


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

    @property
    def kind(self) -> IntentKind:
        return IntentKind.UNKNOWN_COMMAND
