"""设置文件里的 permissions / sandbox / autoMode 段 (ADR-0055 决策 2, 7; ADR-0058
决策 2).

这几段是嵌套对象与列表, 与 `settings/config_store.py` 那套 "dotted key -> 字符串"
的形状对不上, 所以自己读写: 只碰这几个键, 文件里其余内容原样保留.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from forgecli.common.errors import ConfigError
from forgecli.common.json_io import read_document, write_document
from forgecli.core.permissions import PermissionBehavior
from forgecli.core.sandbox import SandboxSettings
from forgecli.settings.paths import config_file, projects_dir

__all__ = [
    "AutoModeRules",
    "PermissionsSection",
    "permissions_section",
    "project_settings_file",
    "read_auto_mode",
    "read_permissions",
    "add_sandbox_allowed_domain",
    "read_sandbox",
    "user_settings_file",
    "write_permissions",
]

# 设置文件里的键名.
_ALLOW = "allow"
_DENY = "deny"
_ASK = "ask"
_ADDITIONAL = "additionalDirectories"
_DEFAULT_MODE = "defaultMode"
_DISABLE_BYPASS = "disableBypassPermissionsMode"
_DISABLE_AUTO = "disableAutoMode"


def user_settings_file() -> Path:
    return config_file("config.json")


def project_settings_file(project_id: str) -> Path:
    return projects_dir() / project_id / "forge.json"


@dataclass(frozen=True)
class PermissionsSection:
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    ask: tuple[str, ...] = ()
    additional_directories: tuple[str, ...] = ()
    default_mode: str = ""
    disable_bypass_permissions_mode: bool = False
    disable_auto_mode: bool = False

    def rules(self, behavior: PermissionBehavior) -> tuple[str, ...]:
        if behavior is PermissionBehavior.ALLOW:
            return self.allow
        if behavior is PermissionBehavior.DENY:
            return self.deny
        return self.ask


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return tuple(out)


def _document(path: Path) -> dict[str, Any]:
    try:
        return read_document(path, label="设置文件")
    except ConfigError:
        return {}


def permissions_section(document: Mapping[str, Any]) -> PermissionsSection:
    raw = document.get("permissions")
    if not isinstance(raw, Mapping):
        return PermissionsSection()
    default_mode = raw.get(_DEFAULT_MODE)
    if not isinstance(default_mode, str):
        default_mode = ""
    return PermissionsSection(
        allow=_strings(raw.get(_ALLOW)),
        deny=_strings(raw.get(_DENY)),
        ask=_strings(raw.get(_ASK)),
        additional_directories=_strings(raw.get(_ADDITIONAL)),
        default_mode=default_mode,
        disable_bypass_permissions_mode=raw.get(_DISABLE_BYPASS) == "disable",
        disable_auto_mode=raw.get(_DISABLE_AUTO) == "disable",
    )


def read_permissions(path: Path) -> PermissionsSection:
    return permissions_section(_document(path))


def write_permissions(path: Path, section: PermissionsSection) -> None:
    """只改 permissions 段里的三张表与目录, 其余键 (defaultMode 等) 保留."""
    document = _document(path)
    raw = document.get("permissions")
    if not isinstance(raw, dict):
        raw = {}
    raw[_ALLOW] = list(section.allow)
    raw[_DENY] = list(section.deny)
    raw[_ASK] = list(section.ask)
    raw[_ADDITIONAL] = list(section.additional_directories)
    document["permissions"] = raw
    write_document(path, document)


def read_sandbox(path: Path) -> SandboxSettings | None:
    """文件里的 sandbox 段. 没有这一段返回 None
    (用缺省)."""
    raw = _document(path).get("sandbox")
    if not isinstance(raw, Mapping):
        return None
    defaults = SandboxSettings()
    filesystem = raw.get("filesystem")
    if not isinstance(filesystem, Mapping):
        filesystem = {}
    network = raw.get("network")
    if not isinstance(network, Mapping):
        network = {}
    return SandboxSettings(
        enabled=bool(raw.get("enabled", defaults.enabled)),
        auto_allow_bash_if_sandboxed=bool(
            raw.get("autoAllowBashIfSandboxed", defaults.auto_allow_bash_if_sandboxed)
        ),
        excluded_commands=_strings(raw.get("excludedCommands")),
        allow_unsandboxed_commands=bool(
            raw.get("allowUnsandboxedCommands", defaults.allow_unsandboxed_commands)
        ),
        allowed_domains=_strings(network.get("allowedDomains")),
        denied_domains=_strings(network.get("deniedDomains")),
        allow_unix_sockets=_strings(network.get("allowUnixSockets")),
        allow_all_unix_sockets=bool(network.get("allowAllUnixSockets", False)),
        allow_local_binding=bool(network.get("allowLocalBinding", False)),
        allow_write=_strings(filesystem.get("allowWrite")),
        deny_write=_strings(filesystem.get("denyWrite")),
        deny_read=_strings(filesystem.get("denyRead")),
        allow_read=_strings(filesystem.get("allowRead")),
    )


def add_sandbox_allowed_domain(path: Path, domain: str) -> None:
    """代理询问卡片的"允许并写进项目设置": 追加进 sandbox.network.allowedDomains, 其余
    键保留."""
    document = _document(path)
    raw = document.get("sandbox")
    if not isinstance(raw, dict):
        raw = {}
    network = raw.get("network")
    if not isinstance(network, dict):
        network = {}
    domains = list(_strings(network.get("allowedDomains")))
    if domain not in domains:
        domains.append(domain)
    network["allowedDomains"] = domains
    raw["network"] = network
    document["sandbox"] = raw
    write_document(path, document)


@dataclass(frozen=True)
class AutoModeRules:
    """设置文件 autoMode 段: 给分类器的三组补充说明 (ADR-0058 决策 2), 每条一句自然语言.

    allow: 明确允许的操作; soft_deny: 除非用户明确要求, 否则拦下的操作; environment:
    关于这个环境的事实 (哪些目录是一次性的, 哪些服务是本地的). 用户级与项目级合并,
    项目级的排在后面.
    """

    allow: tuple[str, ...] = ()
    soft_deny: tuple[str, ...] = ()
    environment: tuple[str, ...] = ()

    def merged_with(self, other: AutoModeRules) -> AutoModeRules:
        return AutoModeRules(
            allow=self.allow + other.allow,
            soft_deny=self.soft_deny + other.soft_deny,
            environment=self.environment + other.environment,
        )


def read_auto_mode(path: Path) -> AutoModeRules:
    raw = _document(path).get("autoMode")
    if not isinstance(raw, Mapping):
        return AutoModeRules()
    return AutoModeRules(
        allow=_strings(raw.get("allow")),
        soft_deny=_strings(raw.get("soft_deny")),
        environment=_strings(raw.get("environment")),
    )
