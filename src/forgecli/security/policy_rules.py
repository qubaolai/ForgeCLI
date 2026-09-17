"""policy_settings 来源的内置规则 (ADR-0055 差异表).

Claude Code 的 policySettings 是企业下发的文件; 本仓库是单用户工具, 这一来源的内容
写在代码里: 凭证目录与 Forge 自己的目录不该等用户自己写 deny 规则才受保护. 来源的
语义与 Claude Code 同 —— 不可被其他来源覆盖, /permissions 里只读.
"""

from __future__ import annotations

import os
from pathlib import Path

from forgecli.core.tool.names import FILE_EDIT_TOOL_NAME, FILE_READ_TOOL_NAME
from forgecli.settings.paths import config_dir

__all__ = ["POLICY_DENY_RULES", "protected_read_paths"]

_CREDENTIAL_PATTERNS: tuple[str, ...] = (
    "~/.ssh/**",
    "~/.aws/**",
    "~/.gnupg/**",
    "~/.kube/**",
    "~/.docker/config.json",
    "~/.netrc",
    "~/.npmrc",
    "~/.pypirc",
    "~/.git-credentials",
    "//etc/shadow",
    "//etc/sudoers",
    "//etc/sudoers.d/**",
)


def _read_rules() -> tuple[str, ...]:
    rules: list[str] = []
    for pattern in _CREDENTIAL_PATTERNS:
        rules.append(f"{FILE_READ_TOOL_NAME}({pattern})")
    return tuple(rules)


def _edit_rules() -> tuple[str, ...]:
    rules: list[str] = []
    for pattern in _CREDENTIAL_PATTERNS:
        rules.append(f"{FILE_EDIT_TOOL_NAME}({pattern})")
    rules.append(f"{FILE_EDIT_TOOL_NAME}(~/.forge/**)")
    return tuple(rules)


# 读与写各一份. Edit 规则同时管 Write.
POLICY_DENY_RULES: tuple[str, ...] = _read_rules() + _edit_rules()


def protected_read_paths() -> tuple[str, ...]:
    """给围栏的 denied_read_paths: 凭证目录与 Forge 自己的目录, 只列这台机器上存在的.

    与 POLICY_DENY_RULES 同一张清单: 规则拦的是工具读, 围栏拦的是子进程读, 漏一边就
    是凭证暴露.
    """
    paths: list[str] = []
    for pattern in _CREDENTIAL_PATTERNS:
        raw = pattern
        if raw.startswith("//"):
            raw = raw[1:]
        for suffix in ("/**", "/*"):
            if raw.endswith(suffix):
                raw = raw[: -len(suffix)]
        candidate = os.path.expanduser(raw)
        if Path(candidate).exists():
            paths.append(str(Path(candidate).resolve()))
    paths.append(str(config_dir()))
    return tuple(dict.fromkeys(paths))
