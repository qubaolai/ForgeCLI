"""多层交互菜单的数据模型(纯结构 + 回调，不含渲染 / 按键)。

驱动逻辑在 interfaces 层的 MenuPresenter：导航方式是 UI 细节，application 只描述
"有哪些项、每项做什么"。各行为字段都可选，Presenter 按是否存在决定按键含义：
    submenu  -> →/Enter 下钻(多层)
    on_cycle -> ←/→ 原地切换(开关 / 枚举)
    on_text  -> →/Enter 行内文本编辑，提交回调
    preview  -> 右侧当前值(每次渲染重读)
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Choice:
    label: str
    preview: Callable[[], str] | None = None
    submenu: Callable[[], Menu] | None = None
    on_cycle: Callable[[int], None] | None = None
    on_text: Callable[[str], None] | None = None
    text_default: Callable[[], str] | None = None


@dataclass(frozen=True)
class Menu:
    title: str
    choices: Sequence[Choice]
