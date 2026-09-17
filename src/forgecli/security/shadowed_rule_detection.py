"""加载时发现 allow 规则被同工具的 deny 规则整个盖住, 记一条 warning (对应
shadowedRuleDetection.ts)."""

from __future__ import annotations

from forgecli.common.observability.log import get_log
from forgecli.core.permissions import ToolPermissionContext, rule_text
from forgecli.security.permissions import get_allow_rules, get_deny_rules

__all__ = ["warn_shadowed_rules"]

_log = get_log(__name__)


def warn_shadowed_rules(context: ToolPermissionContext) -> None:
    whole_tool_denies: set[str] = set()
    for rule in get_deny_rules(context):
        if rule.rule_value.rule_content is None:
            whole_tool_denies.add(rule.rule_value.tool_name)
    for rule in get_allow_rules(context):
        if rule.rule_value.tool_name in whole_tool_denies:
            _log.warning(
                "permissions.shadowed_rule",
                rule=rule_text(rule),
                source=rule.source.value,
            )
