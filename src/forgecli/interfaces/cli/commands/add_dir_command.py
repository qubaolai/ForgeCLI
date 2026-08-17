"""/add-dir: 命令式目录授权 (ADR-0014 §7).

    /add-dir <path>            默认只读
    /add-dir <path> --write    读写, 需要显式确认
    /add-dir --list            列出已授权目录
    /add-dir --remove <path>   撤销授权

改成命令式而不是纯交互面板 (ADR-0014 方案 D): 交互式输入没法脚本化, 也没法在审计里说清
"用户到底授了什么权"; 更要命的是读权限与写权限在一问一答里很容易混淆.

三条不能松的语义:

- **默认只读.** 写权限必须显式 `--write`.
- **受保护路径不能授权.** 凭证, 系统目录和 Forge 自身数据目录挡在 WorkspaceGrants 里.
- **只能由用户发起.** 它是 slash command, 不在工具目录里, LLM 请求不到.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from forgecli.application.interaction_ports import UserOutput
from forgecli.application.project import (
    ProjectContext,
    ProjectService,
    WorkspaceError,
)
from forgecli.application.security.workspace_grants import (
    GrantAccess,
    GrantError,
    WorkspaceGrants,
)
from forgecli.application.slash_commands import CommandHandler
from forgecli.domain.intents import SlashCommand

__all__ = ["AddDirCommand"]


class AddDirCommand(CommandHandler):
    def __init__(
        self,
        context: ProjectContext,
        service: ProjectService,
        grants: WorkspaceGrants,
        output: UserOutput,
    ) -> None:
        self._context = context
        self._service = service
        self._grants = grants
        self._output = output

    def execute(self, command: SlashCommand) -> bool:
        args = command.args
        if "--list" in args:
            return self._list()
        if "--remove" in args:
            return self._remove(_positional(args))
        return self._grant(_positional(args), write="--write" in args)

    # ---- 子命令 ----

    def _list(self) -> bool:
        grants = self._grants.grants
        if not grants:
            self._output.print("尚未授权任何额外目录.")
            return False
        for grant in grants:
            self._output.print(f"{grant.access.value:<5} {grant.path}")
        self._output.print(f"策略版本: {self._grants.policy_version}")
        return False

    def _grant(self, raw: tuple[str, ...], *, write: bool) -> bool:
        if not raw:
            self._output.print("用法: /add-dir <path> [--write]")
            return False
        target = self._normalize(raw[0])
        if target is None:
            return False
        access = GrantAccess.WRITE if write else GrantAccess.READ
        try:
            grant = self._grants.grant(str(target), access, granted_at=_now())
        except GrantError as exc:
            self._output.print(exc.message)
            return False
        self._output.print(
            f"已授权 {grant.access.value}: {grant.path}"
            f" (策略版本 {self._grants.policy_version}, 仅对后续执行生效)"
        )
        return True

    def _remove(self, raw: tuple[str, ...]) -> bool:
        if not raw:
            self._output.print("用法: /add-dir --remove <path>")
            return False
        target = self._normalize(raw[0])
        if target is None:
            return False
        if not self._grants.revoke(str(target)):
            self._output.print(f"该目录未被授权: {target}")
            return False
        self._output.print(
            f"已撤销授权: {target} (策略版本 {self._grants.policy_version})"
        )
        return True

    def _normalize(self, raw: str) -> Path | None:
        """规范化并解析最终目标.

        必须 resolve: 用符号链接指向 /etc 再 /add-dir, 字面路径看起来人畜无害.
        """
        try:
            path = self._service.normalize_workspace_dir(raw, Path.cwd())
        except WorkspaceError as exc:
            self._output.print(exc.message)
            return None
        return path.resolve()


def _positional(args: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(arg for arg in args if not arg.startswith("--"))


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
