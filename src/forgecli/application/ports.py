"""application 层交互端口。

把“向用户提问 / 输出”抽象成协议，由 interface 层提供 Rich 或其他终端适配器。
命令处理器和菜单运行期只依赖这些协议，避免 application 反向依赖 CLI 细节。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from forgecli.application.menu import Menu


class Prompter(ABC):
    """交互式输入端口，所有 stdin 交互都经由它。"""

    @abstractmethod
    def select(self, title: str, items: Sequence[str]) -> str | None:
        """单选菜单：返回所选项文本，返回 None 表示返回上一层或取消。"""

    @abstractmethod
    def confirm(self, label: str, *, default: bool) -> bool:
        """是 / 否开关。"""

    @abstractmethod
    def ask_text(self, label: str, *, default: str | None = None) -> str | None:
        """要求输入文本；返回 None 表示取消。"""


class Output(ABC):
    """输出端口；命令处理器用它回显结果，不直接依赖 Rich Console。"""

    @abstractmethod
    def print(self, message: str) -> None:
        """回显一条消息给用户。"""


class MenuPresenter(ABC):
    """交互式展示并驱动一个可多层展开的菜单，直到用户在根层退出。"""

    @abstractmethod
    def present(self, menu: Menu) -> None:
        """展示并驱动菜单，直到用户在根层退出。"""
