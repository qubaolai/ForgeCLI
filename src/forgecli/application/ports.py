"""application 层交互端口

把 "向用户提问 / 输出" 抽象成协议, 有 interface 层提供Rich 适配器.
命令处理器、菜单运行期只依赖协议
"""

from __future__ import annotations

from abc import ABC
from typing import Sequence

from forgecli.application.menu import Menu

class Prompter(ABC):
    """交互式输入端口, 所有 stdin 交互都经由它."""

    def select(self, title: str, items: Sequence[str]) -> str | None:
        """单选菜单: 返回所选项文本, 返回 None 表示返回上一层或取消"""
        ...

    def  confirm(self, label: str, *, default: bool) -> bool:
        """是 | 否 开关"""
        ...

    def ask_text(self, label: str, *, default: str | None = None) -> str | None:
        """要求输入文本; 返回 None 表示取消"""
        ...

class Output(ABC):
    """输出端口. 命令处理器用它回显结果, 不直接依赖 Rich Console."""
    
    def print(self, message: str) -> None:
        ...

class MenuPresenter(ABC):
    """交互式展示并驱动一个(可多层的)菜单，直到用户在根层退出。"""

    def present(self, menu: Menu) -> None: ...