"""文件工具的裁决 (ADR-0055 决策 5; 对应 utils/permissions/filesystem.ts).

读 (`check_read_permission_for_tool`) 与写 (`check_write_permission_for_tool`) 各一条
固定顺序. 路径规则 `Read(...)` / `Edit(...)` 的写法: `//x` 绝对, `~/x` 家目录, `/x`
相对该规则来源的根, `x` 或 `./x` 相对当前 cwd; `**` 跨目录.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from wcmatch import glob as wcglob

from forgecli.core.permissions import (
    PermissionBehavior,
    PermissionMode,
    PermissionResult,
    PermissionRule,
    PermissionRuleSource,
    PermissionUpdate,
    PermissionUpdateDestination,
    PermissionUpdateType,
    ToolPermissionContext,
    allow,
    ask,
    deny,
    mode_reason,
    other_reason,
    rule_reason,
    safety_check_reason,
    working_dir_reason,
)
from forgecli.core.tool.names import FILE_EDIT_TOOL_NAME, FILE_READ_TOOL_NAME
from forgecli.core.tool.tool import Tool, ToolUseContext
from forgecli.security.permissions import get_allow_rules, get_ask_rules, get_deny_rules
from forgecli.settings.paths import config_dir

__all__ = [
    "DANGEROUS_DIRECTORIES",
    "DANGEROUS_FILES",
    "all_working_directories",
    "check_path_safety_for_auto_edit",
    "check_read_permission_for_tool",
    "check_write_permission_for_tool",
    "expand_path",
    "generate_suggestions",
    "is_forge_settings_path",
    "matching_rule_for_input",
    "path_in_allowed_working_path",
    "path_in_working_path",
    "paths_for_permission_check",
]

# 不许自动改的文件与目录 (对应 DANGEROUS_FILES / DANGEROUS_DIRECTORIES).
DANGEROUS_FILES: tuple[str, ...] = (
    ".gitconfig",
    ".gitmodules",
    ".bashrc",
    ".bash_profile",
    ".zshrc",
    ".zprofile",
    ".profile",
    ".ripgreprc",
    ".mcp.json",
    ".forge.json",
)
DANGEROUS_DIRECTORIES: tuple[str, ...] = (
    ".git",
    ".vscode",
    ".idea",
    ".forge",
    ".claude",
)


def expand_path(path: str, cwd: str) -> str:
    """`~` 展开, 相对路径按 cwd 补齐, 归一化. 不 resolve 软链接."""
    expanded = os.path.expanduser(path)
    if not os.path.isabs(expanded):
        expanded = os.path.join(cwd, expanded)
    return os.path.normpath(expanded)


def paths_for_permission_check(path: str, cwd: str) -> tuple[str, ...]:
    """原路径与 realpath 都要查: 软链接指到别处也逃不掉规则."""
    absolute = expand_path(path, cwd)
    try:
        real = str(Path(absolute).resolve())
    except OSError:
        real = absolute
    if real == absolute:
        return (absolute,)
    return (absolute, real)


def _normalize_for_compare(path: str) -> str:
    normalized = path
    if normalized.startswith("/private/var/"):
        normalized = "/var/" + normalized[len("/private/var/") :]
    if normalized == "/private/tmp" or normalized.startswith("/private/tmp/"):
        normalized = "/tmp" + normalized[len("/private/tmp") :]
    return os.path.normcase(normalized)


def path_in_working_path(path: str, working_path: str, cwd: str) -> bool:
    absolute = _normalize_for_compare(expand_path(path, cwd))
    working = _normalize_for_compare(expand_path(working_path, cwd))
    if absolute == working:
        return True
    return absolute.startswith(working.rstrip(os.sep) + os.sep)


def all_working_directories(
    permission: ToolPermissionContext, cwd: str, workspace_roots: tuple[str, ...]
) -> tuple[str, ...]:
    roots: list[str] = [cwd]
    for root in workspace_roots:
        if root not in roots:
            roots.append(root)
    for directory in permission.additional_working_directories:
        if directory.path not in roots:
            roots.append(directory.path)
    return tuple(roots)


def path_in_allowed_working_path(path: str, context: ToolUseContext) -> bool:
    permission = context.get_app_state().tool_permission_context
    working = all_working_directories(permission, context.cwd, context.workspace_roots)
    for candidate in paths_for_permission_check(path, context.cwd):
        inside = False
        for root in working:
            if path_in_working_path(candidate, root, context.cwd):
                inside = True
                break
        if not inside:
            return False
    return True


# ---- 路径规则 ----


@dataclass(frozen=True)
class _PatternWithRoot:
    relative_pattern: str
    root: str | None


def _root_for_source(source: PermissionRuleSource, context: ToolUseContext) -> str:
    if source in (
        PermissionRuleSource.USER_SETTINGS,
        PermissionRuleSource.POLICY_SETTINGS,
    ):
        return str(Path.home())
    if source is PermissionRuleSource.PROJECT_SETTINGS:
        return context.workspace_roots[0] if context.workspace_roots else context.cwd
    return context.cwd


def _pattern_with_root(
    pattern: str, source: PermissionRuleSource, context: ToolUseContext
) -> _PatternWithRoot:
    if pattern.startswith("//"):
        return _PatternWithRoot(relative_pattern=pattern[1:], root="/")
    if pattern.startswith("~/"):
        return _PatternWithRoot(relative_pattern=pattern[1:], root=str(Path.home()))
    if pattern.startswith("/"):
        return _PatternWithRoot(
            relative_pattern=pattern, root=_root_for_source(source, context)
        )
    normalized = pattern
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return _PatternWithRoot(relative_pattern=normalized, root=None)


def _rules_for(
    permission: ToolPermissionContext, tool_type: str, behavior: PermissionBehavior
) -> tuple[PermissionRule, ...]:
    tool_name = FILE_EDIT_TOOL_NAME if tool_type == "edit" else FILE_READ_TOOL_NAME
    if behavior is PermissionBehavior.ALLOW:
        rules = get_allow_rules(permission)
    elif behavior is PermissionBehavior.DENY:
        rules = get_deny_rules(permission)
    else:
        rules = get_ask_rules(permission)
    matched: list[PermissionRule] = []
    for rule in rules:
        if rule.rule_value.tool_name == tool_name and rule.rule_value.rule_content:
            matched.append(rule)
    return tuple(matched)


def _glob_hit(relative: str, pattern: str) -> bool:
    flags = wcglob.GLOBSTAR | wcglob.DOTGLOB
    candidate = pattern.lstrip("/")
    if wcglob.globmatch(relative, candidate, flags=flags):
        return True
    if candidate.endswith("/**"):
        return wcglob.globmatch(relative, candidate[:-3], flags=flags)
    # 匹配到目录就等于匹配到它下面的一切 (gitignore 的语义).
    return wcglob.globmatch(relative, candidate + "/**", flags=flags)


def matching_rule_for_input(
    path: str, context: ToolUseContext, tool_type: str, behavior: PermissionBehavior
) -> PermissionRule | None:
    """`Read(...)` / `Edit(...)` 的路径规则里第一条命中这个路径的."""
    permission = context.get_app_state().tool_permission_context
    return _matching_rule(path, permission, context, tool_type, behavior)


def _matching_rule(
    path: str,
    permission: ToolPermissionContext,
    context: ToolUseContext,
    tool_type: str,
    behavior: PermissionBehavior,
) -> PermissionRule | None:
    absolute = expand_path(path, context.cwd)
    for rule in _rules_for(permission, tool_type, behavior):
        content = rule.rule_value.rule_content or ""
        located = _pattern_with_root(content, rule.source, context)
        root = located.root
        if root is None:
            root = context.cwd
        try:
            relative = os.path.relpath(absolute, root)
        except ValueError:
            continue
        if relative.startswith(".." + os.sep) or relative == "..":
            continue
        if relative == ".":
            continue
        if _glob_hit(relative.replace(os.sep, "/"), located.relative_pattern):
            return rule
    return None


# ---- 安全检查 ----


def is_forge_settings_path(path: str, cwd: str) -> bool:
    absolute = expand_path(path, cwd)
    return path_in_working_path(absolute, str(config_dir()), cwd)


def _is_dangerous_file_path_to_auto_edit(path: str, cwd: str) -> bool:
    if path.startswith("\\\\") or path.startswith("//"):
        return True
    absolute = expand_path(path, cwd)
    segments = absolute.split(os.sep)
    for segment in segments:
        for directory in DANGEROUS_DIRECTORIES:
            if segment.lower() == directory.lower():
                return True
    file_name = segments[-1] if segments else ""
    return any(file_name.lower() == dangerous.lower() for dangerous in DANGEROUS_FILES)


def _has_suspicious_windows_pattern(path: str) -> bool:
    if "\\\\?\\" in path or "\\\\.\\" in path or "//?/" in path or "//./" in path:
        return True
    if "..." in path:
        return True
    return path.endswith(".") or path.endswith(" ")


def check_path_safety_for_auto_edit(path: str, cwd: str) -> PermissionResult | None:
    """不许自动改的路径 -> ask, 理由是安全检查 (bypass 也问). 安全时返回 None.

    敏感文件路径 (设置, .git, shell 配置) 标 classifier_approvable: auto 模式下让分类器
    看着上下文裁 (用户明确要求改 .zshrc 就不必问人); 可疑的 Windows 路径不标, 那是在
    绕路径检查, 谁都不该自动放.
    """
    if _has_suspicious_windows_pattern(path):
        return ask(
            f"Path '{path}' contains patterns that require manual approval",
            safety_check_reason("suspicious path pattern"),
        )
    if is_forge_settings_path(path, cwd):
        return ask(
            f"Editing Forge settings ({path}) requires manual approval",
            safety_check_reason("forge settings path", classifier_approvable=True),
        )
    if _is_dangerous_file_path_to_auto_edit(path, cwd):
        return ask(
            f"Editing '{path}' requires manual approval: it is a sensitive "
            "configuration or repository-internal file",
            safety_check_reason("sensitive file path", classifier_approvable=True),
        )
    return None


def _in_internal_paths(path: str, context: ToolUseContext) -> bool:
    for internal in context.internal_paths:
        if path_in_working_path(path, internal, context.cwd):
            return True
    return bool(path_in_working_path(path, context.tool_results_dir, context.cwd))


# ---- 读 ----


def check_read_permission_for_tool(
    tool: Tool, input: Mapping[str, object], context: ToolUseContext
) -> PermissionResult:
    path = tool.get_path(input)
    if path is None:
        path = context.cwd
    cwd = context.cwd
    candidates = paths_for_permission_check(path, cwd)

    # 1. UNC 路径先挡.
    if path.startswith("\\\\") or path.startswith("//"):
        return ask(
            f"Reading '{path}' requires manual approval: UNC paths can leak "
            "credentials",
            safety_check_reason("unc path"),
        )
    # 2. 可疑的 Windows 路径.
    if _has_suspicious_windows_pattern(path):
        return ask(
            f"Reading '{path}' requires manual approval: suspicious path pattern",
            safety_check_reason("suspicious path pattern"),
        )
    # 3. read deny 规则.
    for candidate in candidates:
        rule = matching_rule_for_input(
            candidate, context, "read", PermissionBehavior.DENY
        )
        if rule is not None:
            return deny(
                f"Permission to read {path} has been denied.", rule_reason(rule)
            )
    # 4. read ask 规则.
    for candidate in candidates:
        rule = matching_rule_for_input(
            candidate, context, "read", PermissionBehavior.ASK
        )
        if rule is not None:
            return ask(
                f"Reading {path} requires approval (permission rule)", rule_reason(rule)
            )
    # 5. edit allow 规则视为可读.
    for candidate in candidates:
        rule = matching_rule_for_input(
            candidate, context, "edit", PermissionBehavior.ALLOW
        )
        if rule is not None:
            return allow(input, rule_reason(rule))
    # 6. 工作目录内.
    if path_in_allowed_working_path(path, context):
        return allow(input, working_dir_reason("Path is in a working directory"))
    # 7. 内部路径.
    if _in_internal_paths(expand_path(path, cwd), context):
        return allow(input, other_reason("Internal Forge path"))
    # 8. read allow 规则.
    for candidate in candidates:
        rule = matching_rule_for_input(
            candidate, context, "read", PermissionBehavior.ALLOW
        )
        if rule is not None:
            return allow(input, rule_reason(rule))
    # 12. 其余问, 建议加目录.
    return ask(
        f"Forge requested permissions to read from {path}, but you haven't granted "
        "it yet.",
        working_dir_reason("Path is outside the working directories"),
        suggestions=generate_suggestions(path, context, "read"),
        blocked_path=path,
    )


# ---- 写 ----


def check_write_permission_for_tool(
    tool: Tool, input: Mapping[str, object], context: ToolUseContext
) -> PermissionResult:
    path = tool.get_path(input)
    if path is None:
        return ask(
            f"Forge requested permissions to use {tool.name}, but you haven't "
            "granted it yet."
        )
    cwd = context.cwd
    candidates = paths_for_permission_check(path, cwd)
    permission = context.get_app_state().tool_permission_context

    # 1. edit deny 规则.
    for candidate in candidates:
        rule = matching_rule_for_input(
            candidate, context, "edit", PermissionBehavior.DENY
        )
        if rule is not None:
            return deny(
                f"Permission to edit {path} has been denied.", rule_reason(rule)
            )
    absolute = expand_path(path, cwd)
    # 1.5. 内部可写路径.
    if _in_internal_paths(absolute, context):
        return allow(input, other_reason("Internal Forge path"))
    # 1.6. ~/.forge 只认本会话的 allow 规则.
    if is_forge_settings_path(path, cwd):
        session_rules = ToolPermissionContext(
            mode=permission.mode,
            additional_working_directories=permission.additional_working_directories,
            always_allow_rules={
                PermissionRuleSource.SESSION: permission.always_allow_rules.get(
                    PermissionRuleSource.SESSION, ()
                )
            },
        )
        rule = _session_edit_allow(session_rules, absolute, context)
        if rule is not None:
            return allow(input, rule_reason(rule))
    # 1.7. 安全检查.
    safety = check_path_safety_for_auto_edit(path, cwd)
    if safety is not None:
        return safety
    # 2. edit ask 规则.
    for candidate in candidates:
        rule = matching_rule_for_input(
            candidate, context, "edit", PermissionBehavior.ASK
        )
        if rule is not None:
            return ask(
                f"Editing {path} requires approval (permission rule)", rule_reason(rule)
            )
    # 3. accept_edits 且在工作目录内.
    in_working = path_in_allowed_working_path(path, context)
    if permission.mode is PermissionMode.ACCEPT_EDITS and in_working:
        return allow(input, mode_reason(PermissionMode.ACCEPT_EDITS))
    # 4. edit allow 规则.
    for candidate in candidates:
        rule = matching_rule_for_input(
            candidate, context, "edit", PermissionBehavior.ALLOW
        )
        if rule is not None:
            return allow(input, rule_reason(rule))
    # 5. 其余问.
    return ask(
        f"Forge requested permissions to write to {path}, but you haven't granted "
        "it yet.",
        other_reason("This edit requires approval"),
        suggestions=generate_suggestions(path, context, "write"),
        blocked_path=path,
    )


def _session_edit_allow(
    scoped: ToolPermissionContext, absolute: str, context: ToolUseContext
) -> PermissionRule | None:
    return _matching_rule(absolute, scoped, context, "edit", PermissionBehavior.ALLOW)


# ---- 建议 ----


def generate_suggestions(
    path: str, context: ToolUseContext, operation: str
) -> tuple[PermissionUpdate, ...]:
    """ask 决定附带的按钮 (对应 generateSuggestions)."""
    permission = context.get_app_state().tool_permission_context
    updates: list[PermissionUpdate] = []
    outside = not path_in_allowed_working_path(path, context)
    if operation == "write" and permission.mode in (
        PermissionMode.DEFAULT,
        PermissionMode.PLAN,
    ):
        updates.append(
            PermissionUpdate(
                type=PermissionUpdateType.SET_MODE,
                destination=PermissionUpdateDestination.SESSION,
                mode=PermissionMode.ACCEPT_EDITS,
            )
        )
    if outside:
        directory = os.path.dirname(expand_path(path, context.cwd))
        updates.append(
            PermissionUpdate(
                type=PermissionUpdateType.ADD_DIRECTORIES,
                destination=PermissionUpdateDestination.SESSION,
                directories=(directory,),
            )
        )
    return tuple(updates)
