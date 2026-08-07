"""执行环境的领域词汇: 受控 PATH, 环境净化与执行画像.

它不是沙箱. 沙箱回答"获准进程实际能触达什么", 这里回答"进程在什么环境下启动", 以及
"这个环境变了没有" —— 后者是授权信封的绑定项.
"""

from forgecli.domain.execution.environment import (
    DEFAULT_ENV_ALLOWLIST,
    ENVIRONMENT_SANITIZATION_VERSION,
    INJECTION_VARIABLE_PREFIXES,
    INJECTION_VARIABLES,
    ShellLaunch,
    sanitize_environment,
    sanitized_names,
)
from forgecli.domain.execution.profile import ExecutionProfile, IsolationLevel

__all__ = [
    "DEFAULT_ENV_ALLOWLIST",
    "ENVIRONMENT_SANITIZATION_VERSION",
    "INJECTION_VARIABLES",
    "INJECTION_VARIABLE_PREFIXES",
    "ExecutionProfile",
    "IsolationLevel",
    "ShellLaunch",
    "sanitize_environment",
    "sanitized_names",
]
