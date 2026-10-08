"""把权限规则与沙箱设置里的路径解成围栏要的绝对路径 (ADR-0055 决策 7; 对应
sandbox-adapter.ts 的 convertToSandboxRuntimeConfig).

围栏 (`core/execution/fence.py`) 只收绝对路径; 规则里的路径有自己的写法 (`//abs` 绝对,
`~/` 家目录, `/x` 相对于设置文件所属的根, 其余相对工作目录), 那套解释住在
`security/filesystem.py`, 围栏不该再抄一份. 这里做的就是翻译:

- `Edit(...)` allow -> 额外可写; `Edit(...)` deny -> 可写区里挖掉; `Read(...)` deny ->
  子进程也读不到. 规则拦的是工具, 围栏拦的是子进程, 两边不一致就是漏.
- `sandbox.filesystem.*` 用普通路径语义: 绝对就是绝对, `~/` 家目录, 相对则相对主工作区.
- 强制 denyWrite (sandbox-runtime 的 mandatory deny): `~/.forge` (改设置就是改自己的
  权限规则), 每个工作区根下的 `.git/hooks`, `.git/config`, `.vscode`, `.idea` 与 shell /
  git 的 rc 文件. 只列存在的: bubblewrap bind 不存在的路径会报错, 而 `.git/hooks` 在
  `git init` 之前本来就不该拦.

带通配符的规则 (`Edit(src/**/*.py)`) 翻不成一个路径: 尾部的 `/**` `/*` 剥掉, 中间还有
通配就跳过 —— 那条规则只在工具层生效, 围栏层拦不住.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from forgecli.common.runtime_files import runtime_dir
from forgecli.core.permissions import (
    PermissionBehavior,
    PermissionRuleSource,
    ToolPermissionContext,
)
from forgecli.core.tool.names import FILE_EDIT_TOOL_NAME, FILE_READ_TOOL_NAME
from forgecli.security.permissions import get_allow_rules, get_deny_rules
from forgecli.settings.paths import config_dir

__all__ = ["SandboxPaths", "in_sandbox_write_allowlist", "sandbox_paths_for"]

_GLOB_CHARS = ("*", "?", "[")
# 与 sandbox-runtime 的 DANGEROUS_FILES / DANGEROUS_DIRECTORIES 同 (去掉本仓库没有的
# .mcp.json 与 .claude/*). 写它们等于改下一条命令的行为.
_MANDATORY_DENY_WRITE = (
    ".git/hooks",
    ".git/config",
    ".vscode",
    ".idea",
    ".gitconfig",
    ".gitmodules",
    ".bashrc",
    ".bash_profile",
    ".zshrc",
    ".zprofile",
    ".profile",
    ".ripgreprc",
)


@dataclass(frozen=True)
class SandboxPaths:
    extra_writable: tuple[str, ...] = ()
    denied_write: tuple[str, ...] = ()
    denied_read: tuple[str, ...] = ()
    allowed_read: tuple[str, ...] = ()


def sandbox_paths_for(
    permission: ToolPermissionContext,
    *,
    workspace_roots: tuple[str, ...],
    allow_write: tuple[str, ...] = (),
    deny_write: tuple[str, ...] = (),
    deny_read: tuple[str, ...] = (),
    allow_read: tuple[str, ...] = (),
) -> SandboxPaths:
    """`allow_write` 等四个是 `sandbox.filesystem.*` 的原文; 规则从 `permission` 里
    读."""
    primary = workspace_roots[0] if workspace_roots else os.getcwd()
    edit, read = FILE_EDIT_TOOL_NAME, FILE_READ_TOOL_NAME
    allow, deny = PermissionBehavior.ALLOW, PermissionBehavior.DENY
    extra_writable = [
        *_rule_paths(permission, edit, allow, primary),
        *_settings_paths(allow_write, primary),
    ]
    denied_write = [
        *_rule_paths(permission, edit, deny, primary),
        *_settings_paths(deny_write, primary),
        *_mandatory_deny_write(workspace_roots),
    ]
    denied_read = [
        *_rule_paths(permission, read, deny, primary),
        *_settings_paths(deny_read, primary),
    ]
    allowed_read = _settings_paths(allow_read, primary)
    return SandboxPaths(
        extra_writable=tuple(dict.fromkeys(extra_writable)),
        denied_write=tuple(dict.fromkeys(denied_write)),
        denied_read=tuple(dict.fromkeys(denied_read)),
        allowed_read=tuple(dict.fromkeys(allowed_read)),
    )


def in_sandbox_write_allowlist(
    path: str, allow_write: tuple[str, ...], primary: str
) -> bool:
    """`sandbox.filesystem.allowWrite` 明确放开的位置 (对应 pathValidation.ts 的
    isPathInSandboxWriteAllowlist): 工作目录之外的重定向落在这里就不必问. 只看设置里
    写的, 不看自动加进可写集合的工作区根, 否则 accept_edits 那道门就被绕过去了."""
    absolute = os.path.normpath(path)
    for root in _settings_paths(allow_write, primary):
        if absolute == root or absolute.startswith(root.rstrip(os.sep) + os.sep):
            return True
    return False


def _rule_paths(
    permission: ToolPermissionContext,
    tool_name: str,
    behavior: PermissionBehavior,
    primary: str,
) -> list[str]:
    rules = (
        get_allow_rules(permission)
        if behavior is PermissionBehavior.ALLOW
        else get_deny_rules(permission)
    )
    out: list[str] = []
    for rule in rules:
        value = rule.rule_value
        if value.tool_name != tool_name or not value.rule_content:
            continue
        resolved = _rule_path(value.rule_content, rule.source, primary)
        if resolved is not None:
            out.append(resolved)
    return out


def _rule_path(pattern: str, source: PermissionRuleSource, primary: str) -> str | None:
    """规则路径的四种写法 (与 `security/filesystem._pattern_with_root` 同一套解释)."""
    if pattern.startswith("//"):
        candidate = pattern[1:]
    elif pattern.startswith("~/"):
        candidate = os.path.expanduser(pattern)
    elif pattern.startswith("/"):
        user_level = (
            PermissionRuleSource.USER_SETTINGS,
            PermissionRuleSource.POLICY_SETTINGS,
        )
        root = str(Path.home()) if source in user_level else primary
        candidate = root + pattern
    else:
        candidate = os.path.join(primary, pattern.removeprefix("./"))
    return _without_glob(candidate)


def _settings_paths(entries: tuple[str, ...], primary: str) -> list[str]:
    out: list[str] = []
    for entry in entries:
        raw = entry.strip()
        if not raw:
            continue
        if raw.startswith("//"):
            raw = raw[1:]
        candidate = os.path.expanduser(raw)
        if not os.path.isabs(candidate):
            candidate = os.path.join(primary, candidate)
        resolved = _without_glob(candidate)
        if resolved is not None:
            out.append(resolved)
    return out


def _without_glob(candidate: str) -> str | None:
    stripped = candidate
    for suffix in ("/**", "/*"):
        if stripped.endswith(suffix):
            stripped = stripped[: -len(suffix)]
    if any(char in stripped for char in _GLOB_CHARS):
        return None
    return os.path.normpath(stripped)


def _mandatory_deny_write(workspace_roots: tuple[str, ...]) -> list[str]:
    out = [str(config_dir())]
    # 临时运行状态搬离 Forge home 后仍不得由受限 Shell 篡改.
    out.extend(str(runtime_dir() / name) for name in ("projects", "logs", "artifacts"))
    for root in workspace_roots:
        for relative in _MANDATORY_DENY_WRITE:
            candidate = os.path.join(root, relative)
            if os.path.lexists(candidate):
                out.append(candidate)
    return out
