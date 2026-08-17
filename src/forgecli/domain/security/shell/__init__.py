"""Shell 解析: 原始命令 -> 可审计的 CommandPlan (ADR-0013 §3 / §6 / §7).

纯函数, 无 IO, 不执行任何东西 —— 包括不执行解码出来的 PowerShell EncodedCommand.
"""

from forgecli.domain.security.shell.command_plan import (
    CommandPlan,
    CommandUnit,
    Connector,
    ParseStatus,
    Redirect,
    RedirectKind,
    ScriptPayload,
    ShellKind,
    UnitOrigin,
)
from forgecli.domain.security.shell.expansion import (
    ExpansionResult,
    expand_home,
    expand_targets,
)
from forgecli.domain.security.shell.parser import parse_command
from forgecli.domain.security.shell.powershell import decode_encoded_command
from forgecli.domain.security.shell.tokens import ScanError
from forgecli.domain.security.shell.wrappers import (
    INDIRECT_EXECUTORS,
    PRIVILEGE_ESCALATORS,
)

__all__ = [
    "INDIRECT_EXECUTORS",
    "PRIVILEGE_ESCALATORS",
    "CommandPlan",
    "CommandUnit",
    "Connector",
    "ExpansionResult",
    "ParseStatus",
    "Redirect",
    "RedirectKind",
    "ScanError",
    "ScriptPayload",
    "ShellKind",
    "UnitOrigin",
    "decode_encoded_command",
    "expand_home",
    "expand_targets",
    "parse_command",
]
