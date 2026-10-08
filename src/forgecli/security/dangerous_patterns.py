"""会绕过分类器的 allow 规则 (ADR-0058 决策 5; 对应 dangerousPatterns.ts 与
permissionSetup.ts 的 isDangerousBashPermission).

`Bash(python:*)` 这样一条 allow 规则在 default 下只是省几次确认; 在 auto 下它让模型
不经分类器就能跑任意代码 —— 分类器只看得到没被规则放行的调用. 所以进 auto 时把这类
规则剥掉, 出 auto 放回去.
"""

from __future__ import annotations

from forgecli.core.permissions import PermissionRuleValue
from forgecli.core.tool.names import BASH_TOOL_NAME

__all__ = ["DANGEROUS_BASH_PATTERNS", "is_dangerous_bash_permission"]

# 能执行任意代码的入口: 解释器, 包运行器, shell, 远程执行, 提权, 以及把参数变成命令的
# 包装器.
DANGEROUS_BASH_PATTERNS: tuple[str, ...] = (
    "python",
    "python3",
    "python2",
    "node",
    "deno",
    "tsx",
    "ruby",
    "perl",
    "php",
    "lua",
    "npx",
    "bunx",
    "npm run",
    "yarn run",
    "pnpm run",
    "bun run",
    "bash",
    "sh",
    "ssh",
    "zsh",
    "fish",
    "eval",
    "exec",
    "env",
    "xargs",
    "sudo",
)


def is_dangerous_bash_permission(value: PermissionRuleValue) -> bool:
    """整工具的 Bash allow, `Bash(*)`, 以及解释器前缀 / 通配的 allow 都算."""
    if value.tool_name != BASH_TOOL_NAME:
        return False
    if value.rule_content is None or value.rule_content == "":
        return True
    content = value.rule_content.strip().lower()
    if content == "*":
        return True
    for pattern in DANGEROUS_BASH_PATTERNS:
        if content == pattern:
            return True
        if content == f"{pattern}:*":
            return True
        if content == f"{pattern}*":
            return True
        if content == f"{pattern} *":
            return True
        if content.startswith(f"{pattern} -") and content.endswith("*"):
            return True
    return False
