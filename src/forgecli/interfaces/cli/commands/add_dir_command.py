"""/add-dir：交互式添加一个可操作工作区目录。

只做「呈现 + 调用 ProjectService」：面板收集原始路径，归一化 / 校验 / 去重 / 持久化
都在 service 内。非法路径翻成一行友好提示，不抛 traceback。
"""

from __future__ import annotations

from pathlib import Path

from forgecli.application.interaction_ports import DirectoryPicker, UserOutput
from forgecli.application.project import (
    ProjectContext,
    ProjectService,
    WorkspaceError,
)
from forgecli.application.slash_commands import CommandHandler
from forgecli.domain.intents import SlashCommand


class AddDirCommand(CommandHandler):
    def __init__(
        self,
        context: ProjectContext,
        service: ProjectService,
        picker: DirectoryPicker,
        output: UserOutput,
    ) -> None:
        self._context = context
        self._service = service
        self._picker = picker
        self._output = output

    def execute(self, command: SlashCommand) -> bool:
        base = Path.cwd()
        raw = self._picker.pick(lambda text: self._subdirs(base, text))
        if raw is None:  # 取消
            return False
        try:
            path = self._service.normalize_workspace_dir(raw, base)
        except WorkspaceError as exc:
            self._output.print(exc.message)
            return False
        project = self._context.project
        if str(path) in project.workspace_roots:
            self._output.print(f"目录已在工作区列表中：{path}")
            return False
        self._context.project = self._service.add_workspace_dir(project, path)
        self._output.print(f"已添加可操作目录：{path}")
        return True

    @staticmethod
    def _subdirs(base: Path, raw: str = "") -> list[str]:
        # 列出 raw 指向目录（相对 base 或绝对；空则 base 自身）的子目录名。
        text = raw.strip()
        target = Path(text).expanduser() if text else base
        if not target.is_absolute():
            target = base / target
        try:
            return sorted(p.name for p in target.iterdir() if p.is_dir())
        except OSError:
            return []
