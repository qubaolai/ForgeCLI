"""application 层交互端口。

把“向用户提问 / 输出”抽象成协议，由 interface 层提供 Rich 或其他终端适配器。
命令处理器和菜单运行期只依赖这些协议，避免 application 反向依赖 CLI 细节。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence

from forgecli.application.menu import Menu


class UserOutput(ABC):
    """输出端口；命令处理器用它回显结果，不直接依赖 Rich Console。"""

    @abstractmethod
    def print(self, message: str) -> None:
        """回显一条消息给用户。"""


class MenuPresenter(ABC):
    """交互式展示并驱动一个可多层展开的菜单，直到用户在根层退出。"""

    @abstractmethod
    def present(self, menu: Menu) -> None:
        """展示并驱动菜单，直到用户在根层退出。"""


class TrustPrompter(ABC):
    """首启信任确认端口；展示绝对路径并返回用户是否信任。"""

    @abstractmethod
    def confirm(self, path: str) -> bool:
        """询问是否信任 path；是返回 True，否 / 取消返回 False。"""


class DirectoryPicker(ABC):
    """目录输入端口；按需列出子目录供参考，返回用户输入的原始路径。"""

    @abstractmethod
    def pick(self, list_subdirs: Callable[[str], Sequence[str]]) -> str | None:
        """返回用户输入的目录路径（未规范化）；取消返回 None。

        list_subdirs(raw) 按需返回某路径（相对当前目录或绝对）下的子目录名，
        供面板在用户按键时列出参考；传空字符串表示当前目录。
        """
