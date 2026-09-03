"""输入行: 历史, 斜杠命令补全与续行.

用标准库的 ``readline`` 而不是再引一个交互输入库. 理由是这条入口回来时 (ADR-0045)
带回来的东西越少越好: 一个只在 ``interfaces`` 用得到的第三方交互库, 会跟着进
``[project].dependencies``, 进锁文件, 进每一次安装与 CVE 跟踪, 而它换来的是补全菜单
的样式. ``readline`` 给的历史, 行编辑与 Tab 补全已经够用, 且装不装它由平台决定 ——
没有它的平台退回裸 ``input()``, 功能少一截而不是起不来.
"""

from __future__ import annotations

import atexit
import contextlib
from collections.abc import Sequence
from pathlib import Path

# Windows 的 CPython 不带 readline. 少了它只是没有历史与补全, 不该让终端会话起不来.
try:  # pragma: no cover - 是否装得上由平台决定
    import readline
except ImportError:  # pragma: no cover
    readline = None  # type: ignore[assignment]

_HISTORY_LIMIT = 2000
# 续行符. 与 shell 一致: 行尾一个反斜杠表示"这句还没说完".
_CONTINUATION = "\\"


class LineEditor:
    """一次会话共用一个: 历史与补全表都是跨行存活的状态."""

    def __init__(self, history_file: Path, commands: Sequence[str] = ()) -> None:
        self._commands = tuple(sorted(commands))
        self._matches: list[str] = []
        self._enabled = readline is not None
        if not self._enabled:
            return
        self._load_history(history_file)
        readline.set_completer(self._complete)
        # 把斜杠也算进单词: 默认分隔符里有 "/", 于是补全看到的只有 "model" 这一截,
        # 补出来的候选就会把用户已经敲的斜杠再补一遍.
        readline.set_completer_delims(" \t\n")
        readline.parse_and_bind("tab: complete")

    def _load_history(self, history_file: Path) -> None:
        assert readline is not None
        with contextlib.suppress(OSError):
            history_file.parent.mkdir(parents=True, exist_ok=True)
            if history_file.exists():
                readline.read_history_file(str(history_file))
        readline.set_history_length(_HISTORY_LIMIT)
        # 进程正常退出时落盘. 崩溃时丢掉这一段历史是可以接受的代价.
        atexit.register(self._save_history, history_file)

    @staticmethod
    def _save_history(history_file: Path) -> None:  # pragma: no cover - 退出钩子
        assert readline is not None
        with contextlib.suppress(OSError):
            readline.write_history_file(str(history_file))

    def _complete(self, text: str, state: int) -> str | None:
        """只补斜杠命令. 补文件名要读磁盘, 而输入框里绝大多数内容是自然语言."""
        if state == 0:
            self._matches = [name for name in self._commands if name.startswith(text)]
        if state < len(self._matches):
            return self._matches[state]
        return None

    def read(self, prompt: str) -> str:
        """读一条消息.

        行尾反斜杠续行; Ctrl-D 抛 EOFError, Ctrl-C 抛 KeyboardInterrupt —— 两者的
        区别由调用方决定怎么收场.
        """
        lines: list[str] = []
        current = prompt
        while True:
            line = input(current)
            if line.endswith(_CONTINUATION):
                lines.append(line[: -len(_CONTINUATION)])
                current = "… "
                continue
            lines.append(line)
            return "\n".join(lines)
