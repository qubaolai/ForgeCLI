"""PreToolUse / PostToolUse hook (ADR-0054 决策 6; 对应 services/tools/toolHooks.ts).

配置在 config.json / forge.json 的 hooks 段, 形状与 Claude Code 的 settings.json 同:

    {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command",
      "command": "./scripts/check.sh", "timeout": 60}]}]}}

hook 从 stdin 读 JSON, 退出码 0 通过 (stdout 里的 JSON 可以带 permissionDecision /
updatedInput / additionalContext), 2 是 deny (stderr 是给模型的理由), 其余记 warning
后当没表态. hook 的 allow 不越过 deny / ask 规则 —— 那一步在执行链里再过一遍.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from forgecli.common.observability.log import get_log
from forgecli.core.tool.tool import Tool, ToolUseContext
from forgecli.security.permission_rule_parser import permission_rule_value_from_string
from forgecli.security.settings_store import hooks_section

__all__ = ["HookOutcome", "HookRunner", "load_hook_config"]

_log = get_log(__name__)

# 缺省 10 分钟 (TOOL_HOOK_EXECUTION_TIMEOUT_MS), 每条 hook 可以自己配 timeout (秒).
_DEFAULT_TIMEOUT_SECONDS = 600.0
_BLOCK_EXIT_CODE = 2


@dataclass(frozen=True)
class HookCommand:
    command: str
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    # 可选的 `if`: 一条规则写法 (`Bash(git *)`), 用工具的 prepare_permission_matcher 比.
    condition: str = ""


@dataclass(frozen=True)
class HookMatcher:
    event: str  # PreToolUse / PostToolUse
    matcher: str  # 正则, 按工具名匹配; 空 = 全部
    hooks: tuple[HookCommand, ...]


@dataclass(frozen=True)
class HookOutcome:
    """一组 hook 跑完的合并结论."""

    # allow / deny / ask / "" (没表态)
    permission_decision: str = ""
    reason: str = ""
    hook_name: str = ""
    updated_input: Mapping[str, object] | None = None
    additional_contexts: tuple[str, ...] = ()
    blocking_message: str = ""

    @property
    def blocked(self) -> bool:
        return self.permission_decision == "deny" or bool(self.blocking_message)


def _commands_of(raw: object) -> tuple[HookCommand, ...]:
    if not isinstance(raw, list):
        return ()
    commands: list[HookCommand] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        if item.get("type", "command") != "command":
            continue
        command = item.get("command")
        if not isinstance(command, str) or not command.strip():
            continue
        timeout = item.get("timeout")
        seconds = _DEFAULT_TIMEOUT_SECONDS
        if isinstance(timeout, int | float) and timeout > 0:
            seconds = float(timeout)
        condition = item.get("if")
        if not isinstance(condition, str):
            condition = ""
        commands.append(
            HookCommand(command=command, timeout_seconds=seconds, condition=condition)
        )
    return tuple(commands)


def _matchers_of(event: str, raw: object) -> tuple[HookMatcher, ...]:
    if not isinstance(raw, list):
        return ()
    matchers: list[HookMatcher] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        matcher = entry.get("matcher", "")
        if not isinstance(matcher, str):
            matcher = ""
        commands = _commands_of(entry.get("hooks"))
        if commands:
            matchers.append(HookMatcher(event=event, matcher=matcher, hooks=commands))
    return tuple(matchers)


def load_hook_config(files: tuple[Path, ...]) -> tuple[HookMatcher, ...]:
    """用户级与项目级的 hooks 段合起来, 先用户后项目."""
    matchers: list[HookMatcher] = []
    for path in files:
        section = hooks_section(path)
        for event in ("PreToolUse", "PostToolUse"):
            matchers.extend(_matchers_of(event, section.get(event)))
    return tuple(matchers)


def _matches(matcher: str, tool_name: str) -> bool:
    if not matcher or matcher == "*":
        return True
    try:
        return re.fullmatch(matcher, tool_name) is not None
    except re.error:
        return matcher == tool_name


@dataclass
class HookRunner:
    matchers: tuple[HookMatcher, ...] = ()

    def _selected(
        self, event: str, tool: Tool, input: Mapping[str, object]
    ) -> tuple[HookCommand, ...]:
        selected: list[HookCommand] = []
        matcher_prepared = False
        condition_matcher: Callable[[str], bool] | None = None
        for matcher in self.matchers:
            if matcher.event != event or not _matches(matcher.matcher, tool.name):
                continue
            for command in matcher.hooks:
                if command.condition:
                    if not matcher_prepared:
                        condition_matcher = tool.prepare_permission_matcher(input)
                        matcher_prepared = True
                    value = permission_rule_value_from_string(command.condition)
                    if value.tool_name != tool.name:
                        continue
                    content = value.rule_content
                    if (
                        content is not None
                        and condition_matcher is not None
                        and not condition_matcher(content)
                    ):
                        continue
                    # 工具不支持内容匹配时保守起见照跑.
                selected.append(command)
        return tuple(selected)

    def run_pre_tool_use(
        self,
        tool: Tool,
        input: Mapping[str, object],
        context: ToolUseContext,
        *,
        tool_use_id: str,
    ) -> HookOutcome:
        payload = {
            "session_id": context.session_id,
            "cwd": context.cwd,
            "hook_event_name": "PreToolUse",
            "tool_name": tool.name,
            "tool_input": _jsonable(input),
            "tool_use_id": tool_use_id,
        }
        return self._run_all("PreToolUse", tool, input, context, payload)

    def run_post_tool_use(
        self,
        tool: Tool,
        input: Mapping[str, object],
        context: ToolUseContext,
        *,
        tool_use_id: str,
        tool_response: str,
    ) -> HookOutcome:
        payload = {
            "session_id": context.session_id,
            "cwd": context.cwd,
            "hook_event_name": "PostToolUse",
            "tool_name": tool.name,
            "tool_input": _jsonable(input),
            "tool_use_id": tool_use_id,
            "tool_response": tool_response,
        }
        return self._run_all("PostToolUse", tool, input, context, payload)

    def _run_all(
        self,
        event: str,
        tool: Tool,
        input: Mapping[str, object],
        context: ToolUseContext,
        payload: Mapping[str, object],
    ) -> HookOutcome:
        outcome = HookOutcome()
        for command in self._selected(event, tool, input):
            result = self._run_one(command, context, payload)
            outcome = _merge(outcome, result)
            if outcome.blocked:
                return outcome
        return outcome

    def _run_one(
        self,
        command: HookCommand,
        context: ToolUseContext,
        payload: Mapping[str, object],
    ) -> HookOutcome:
        try:
            completed = subprocess.run(  # noqa: S602 - 用户自己配置的命令, 与 Claude Code 同
                command.command,
                shell=True,
                cwd=context.cwd,
                env=dict(context.environment),
                input=json.dumps(payload, ensure_ascii=False),
                capture_output=True,
                text=True,
                timeout=command.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            _log.warning("hooks.timeout", command=command.command)
            return HookOutcome()
        except OSError as exc:
            _log.warning("hooks.failed", command=command.command, error=str(exc))
            return HookOutcome()
        if completed.returncode == _BLOCK_EXIT_CODE:
            reason = completed.stderr.strip() or "blocked by hook"
            return HookOutcome(
                permission_decision="deny",
                reason=reason,
                hook_name=command.command,
                blocking_message=reason,
            )
        if completed.returncode != 0:
            _log.warning(
                "hooks.nonzero_exit",
                command=command.command,
                code=completed.returncode,
                stderr=completed.stderr[:500],
            )
            return HookOutcome()
        return _parse_stdout(command, completed.stdout)


def _parse_stdout(command: HookCommand, stdout: str) -> HookOutcome:
    text = stdout.strip()
    if not text.startswith("{"):
        return HookOutcome()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return HookOutcome()
    if not isinstance(data, Mapping):
        return HookOutcome()
    if data.get("decision") == "block":
        reason = str(data.get("reason", "blocked by hook"))
        return HookOutcome(
            permission_decision="deny",
            reason=reason,
            hook_name=command.command,
            blocking_message=reason,
        )
    specific = data.get("hookSpecificOutput")
    if not isinstance(specific, Mapping):
        return HookOutcome()
    decision = specific.get("permissionDecision", "")
    if decision not in ("allow", "deny", "ask"):
        decision = ""
    reason = str(specific.get("permissionDecisionReason", ""))
    updated = specific.get("updatedInput")
    updated_input = None
    if isinstance(updated, Mapping):
        updated_input = dict(updated)
    contexts: tuple[str, ...] = ()
    additional = specific.get("additionalContext")
    if isinstance(additional, str) and additional.strip():
        contexts = (additional.strip(),)
    blocking = reason if decision == "deny" else ""
    return HookOutcome(
        permission_decision=decision,
        reason=reason,
        hook_name=command.command,
        updated_input=updated_input,
        additional_contexts=contexts,
        blocking_message=blocking,
    )


def _merge(current: HookOutcome, new: HookOutcome) -> HookOutcome:
    decision = current.permission_decision
    reason = current.reason
    name = current.hook_name
    if new.permission_decision:
        # deny 最重, 然后 ask, 然后 allow.
        rank = {"": 0, "allow": 1, "ask": 2, "deny": 3}
        if rank[new.permission_decision] >= rank[decision]:
            decision = new.permission_decision
            reason = new.reason
            name = new.hook_name
    updated = current.updated_input
    if new.updated_input is not None:
        updated = new.updated_input
    return HookOutcome(
        permission_decision=decision,
        reason=reason,
        hook_name=name,
        updated_input=updated,
        additional_contexts=current.additional_contexts + new.additional_contexts,
        blocking_message=new.blocking_message or current.blocking_message,
    )


def _jsonable(value: object) -> object:
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    return str(value)
