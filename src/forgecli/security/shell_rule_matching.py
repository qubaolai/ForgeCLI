"""Shell 规则的三种写法与匹配 (对应 shellRuleMatching.ts).

- `npm test` 精确: 整串相等.
- `git commit:*` 前缀: 等于前缀, 或以 `前缀 + 空格` 开头.
- `git * --force*` 通配: `*` 匹配任意字符, `\\*` 字面星号, `\\\\` 字面反斜杠.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from forgecli.core.permissions import (
    PermissionBehavior,
    PermissionRuleValue,
    PermissionUpdate,
    PermissionUpdateDestination,
    PermissionUpdateType,
)

__all__ = [
    "ShellPermissionRule",
    "has_wildcards",
    "match_wildcard_pattern",
    "parse_permission_rule",
    "permission_rule_extract_prefix",
    "suggestion_for_exact_command",
    "suggestion_for_prefix",
]


@dataclass(frozen=True)
class ShellPermissionRule:
    kind: str  # exact / prefix / wildcard
    value: str


def permission_rule_extract_prefix(rule: str) -> str | None:
    match = re.match(r"^(.+):\*$", rule)
    if match is None:
        return None
    return match.group(1)


def has_wildcards(pattern: str) -> bool:
    if pattern.endswith(":*"):
        return False
    for index, char in enumerate(pattern):
        if char != "*":
            continue
        backslashes = 0
        cursor = index - 1
        while cursor >= 0 and pattern[cursor] == "\\":
            backslashes += 1
            cursor -= 1
        if backslashes % 2 == 0:
            return True
    return False


def match_wildcard_pattern(
    pattern: str, command: str, *, case_insensitive: bool = False
) -> bool:
    regex = _wildcard_regex(pattern.strip())
    flags = re.DOTALL
    if case_insensitive:
        flags = flags | re.IGNORECASE
    return re.match(regex, command.strip(), flags) is not None


def _wildcard_regex(pattern: str) -> str:
    parts: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\" and index + 1 < len(pattern):
            following = pattern[index + 1]
            if following == "*":
                parts.append(re.escape("*"))
                index += 2
                continue
            if following == "\\":
                parts.append(re.escape("\\"))
                index += 2
                continue
        if char == "*":
            parts.append(".*")
        else:
            parts.append(re.escape(char))
        index += 1
    return "^" + "".join(parts) + "$"


def parse_permission_rule(rule: str) -> ShellPermissionRule:
    prefix = permission_rule_extract_prefix(rule)
    if prefix is not None:
        return ShellPermissionRule(kind="prefix", value=prefix)
    if has_wildcards(rule):
        return ShellPermissionRule(kind="wildcard", value=rule)
    return ShellPermissionRule(kind="exact", value=rule)


def suggestion_for_exact_command(
    tool_name: str, command: str
) -> tuple[PermissionUpdate, ...]:
    return (
        PermissionUpdate(
            type=PermissionUpdateType.ADD_RULES,
            destination=PermissionUpdateDestination.PROJECT_SETTINGS,
            rules=(PermissionRuleValue(tool_name=tool_name, rule_content=command),),
            behavior=PermissionBehavior.ALLOW,
        ),
    )


def suggestion_for_prefix(tool_name: str, prefix: str) -> tuple[PermissionUpdate, ...]:
    return (
        PermissionUpdate(
            type=PermissionUpdateType.ADD_RULES,
            destination=PermissionUpdateDestination.PROJECT_SETTINGS,
            rules=(
                PermissionRuleValue(tool_name=tool_name, rule_content=f"{prefix}:*"),
            ),
            behavior=PermissionBehavior.ALLOW,
        ),
    )
