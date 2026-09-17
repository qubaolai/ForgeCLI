"""规则原文 `Bash(git commit:*)` 与 PermissionRuleValue 的互转 (对应
permissionRuleParser.ts).

括号里可以用 `\\(` `\\)` 转义. 没有括号就是整工具规则; `Bash()` 与 `Bash(*)` 也算整
工具. 工具名认曾用名 (`shell_run(...)` 解析成 `Bash(...)`).
"""

from __future__ import annotations

from forgecli.core.permissions import PermissionRuleValue
from forgecli.core.tool.names import normalize_legacy_tool_name

__all__ = [
    "escape_rule_content",
    "permission_rule_value_from_string",
    "permission_rule_value_to_string",
    "unescape_rule_content",
]


def escape_rule_content(content: str) -> str:
    return content.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def unescape_rule_content(content: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(content):
        char = content[index]
        if char == "\\" and index + 1 < len(content):
            following = content[index + 1]
            if following in "()\\":
                out.append(following)
                index += 2
                continue
        out.append(char)
        index += 1
    return "".join(out)


def _first_unescaped(text: str, char: str) -> int:
    for index, current in enumerate(text):
        if current == char and not _escaped_at(text, index):
            return index
    return -1


def _last_unescaped(text: str, char: str) -> int:
    for index in range(len(text) - 1, -1, -1):
        if text[index] == char and not _escaped_at(text, index):
            return index
    return -1


def _escaped_at(text: str, index: int) -> bool:
    backslashes = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return backslashes % 2 == 1


def permission_rule_value_from_string(rule_string: str) -> PermissionRuleValue:
    text = rule_string.strip()
    open_index = _first_unescaped(text, "(")
    if open_index == -1:
        return PermissionRuleValue(tool_name=normalize_legacy_tool_name(text))
    close_index = _last_unescaped(text, ")")
    if close_index == -1 or close_index <= open_index:
        return PermissionRuleValue(tool_name=normalize_legacy_tool_name(text))
    if close_index != len(text) - 1:
        return PermissionRuleValue(tool_name=normalize_legacy_tool_name(text))
    tool_name = text[:open_index]
    raw_content = text[open_index + 1 : close_index]
    if not tool_name:
        return PermissionRuleValue(tool_name=normalize_legacy_tool_name(text))
    if raw_content == "" or raw_content == "*":
        return PermissionRuleValue(tool_name=normalize_legacy_tool_name(tool_name))
    return PermissionRuleValue(
        tool_name=normalize_legacy_tool_name(tool_name),
        rule_content=unescape_rule_content(raw_content),
    )


def permission_rule_value_to_string(value: PermissionRuleValue) -> str:
    if not value.rule_content:
        return value.tool_name
    return f"{value.tool_name}({escape_rule_content(value.rule_content)})"
