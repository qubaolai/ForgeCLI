"""可执行文件身份解析 (ADR-0013 §5.1).

裁决和执行必须指向**同一个文件**. 这里做三件事:

1. 只在**受控 PATH** 里查找. 不查 os.environ, 不查工作区, 不查 `.` —— 那些位置 Agent
   自己能写.
2. 解析到绝对 realpath 并读取文件身份与内容哈希. 名字相同不代表文件相同.
3. 判定信任区. 工作区内, 临时目录和用户配置的工具链目录都算 Agent 可写, 里面的
   executable 不具备普通 Allow 资格, 只能按脚本执行分析.

shebang 解释器链也一并解出: `./deploy.sh` 的真实执行者是它第一行写的那个解释器.
"""

from __future__ import annotations

from pathlib import PurePath

from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.application.workspace.filesystem_view import PathKind
from forgecli.domain.security.executable_identity import ExecutableIdentity, TrustZone
from forgecli.domain.tool.hashing import digest_text
from forgecli.domain.workspace.boundary import is_within

__all__ = ["ExecutableResolver"]

_MAX_HASH_BYTES = 4 * 1024 * 1024
_SHEBANG_MAX = 256
_TEMP_ROOTS = ("/tmp", "/private/tmp", "/var/tmp", "/dev/shm")


class ExecutableResolver:
    """把命令名解析成可绑定的文件身份."""

    def resolve(self, token: str, context: ExecutionContext) -> ExecutableIdentity:
        absolute = self._locate(token, context)
        if absolute is None:
            return ExecutableIdentity.unresolved(token)
        facts = context.filesystem.facts(absolute)
        if facts.kind is not PathKind.FILE:
            return ExecutableIdentity.unresolved(token)
        content = context.filesystem.read_bytes(
            facts.realpath, max_bytes=_MAX_HASH_BYTES
        )
        return ExecutableIdentity(
            requested_token=token,
            absolute_path=absolute,
            realpath=facts.realpath,
            file_identity=facts.file_identity,
            content_hash=digest_text(content.decode("utf-8", errors="replace")),
            trust_zone=self._trust_zone(facts.realpath, context),
            interpreter_chain=self._interpreter_chain(content),
        )

    def _locate(self, token: str, context: ExecutionContext) -> str | None:
        if "/" in token or "\\" in token:
            return context.resolve(token)
        raw_path = context.environment.get("PATH", "")
        separator = ";" if "\\" in raw_path else ":"
        for entry in raw_path.split(separator):
            # PATH 里出现 `.` 本身就是配置错误 (ExecutionProfile 会拒绝), 这里再挡一次.
            if not entry or entry == ".":
                continue
            candidate = f"{entry.rstrip('/')}/{token}"
            if context.filesystem.facts(candidate).kind is PathKind.FILE:
                return candidate
        return None

    def _trust_zone(self, realpath: str, context: ExecutionContext) -> TrustZone:
        if any(is_within(realpath, root) for root in context.workspace_roots):
            return TrustZone.WORKSPACE
        if any(is_within(realpath, root) for root in _TEMP_ROOTS):
            return TrustZone.WORKSPACE
        if _is_project_toolchain(realpath):
            return TrustZone.TOOLCHAIN
        if any(is_within(realpath, entry) for entry in context.profile.trusted_path):
            return TrustZone.SYSTEM
        return TrustZone.UNKNOWN

    def _interpreter_chain(self, content: bytes) -> tuple[str, ...]:
        head = content[:_SHEBANG_MAX].decode("utf-8", errors="replace")
        if not head.startswith("#!"):
            return ()
        first_line = head.splitlines()[0][2:].strip()
        parts = first_line.split()
        if not parts:
            return ()
        # `#!/usr/bin/env python3` 的真实解释器是 python3, 两段都要留档.
        if parts[0].endswith("/env") and len(parts) > 1:
            return (parts[0], parts[1])
        return (parts[0],)


# 项目本地工具链: Agent 能写进去, 因此不能凭名字继承系统工具的授权.
_TOOLCHAIN_MARKERS = (
    "/node_modules/.bin",
    "/.venv/",
    "/venv/",
    "/.tox/",
    "/vendor/bin",
    "/.bundle/",
)


def _is_project_toolchain(realpath: str) -> bool:
    normalized = PurePath(realpath).as_posix()
    return any(marker in normalized for marker in _TOOLCHAIN_MARKERS)
