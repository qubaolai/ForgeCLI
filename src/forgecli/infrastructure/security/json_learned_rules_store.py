"""学习规则的 JSON 落盘.

规则跨会话有效, 所以必须存起来. 落在 Forge 状态目录而不是工作区: Agent 和它起的
子进程都不该能改自己的授权规则 —— 能改就等于能给自己发许可.

匹配用的字段一律只存**哈希** (命令, 脚本内容, 可执行文件身份). 匹配靠逐项比对哈希,
不需要原文; 而原文里可能有路径, 主机名甚至凭证片段.

唯一的例外是 label —— 可执行文件的基名 (`git`, `npm`). 它不参与匹配, 存在的理由只有
一个: /rules 列表里用户得认得出哪条是哪条. 基名带不出凭证或主机名, 参数才会, 所以
参数在任何情况下都不落盘.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from forgecli.application.security.learned_rules import LearnedRuleStore
from forgecli.domain.intents import SessionMode
from forgecli.domain.security.rules import LearnedAllowRule, RuleMatch
from forgecli.domain.security.vocabulary import ApprovalScope
from forgecli.infrastructure.json_io import read_document, write_document

__all__ = ["JsonLearnedRuleStore"]

_RULES = "rules"
_MATCH_FIELDS = (
    "tool_name",
    "plan_hash",
    "command_hash",
    "executable_identity_hash",
    "target_set_hash",
    "workspace_id",
    "policy_version",
    "execution_profile_hash",
)


class JsonLearnedRuleStore(LearnedRuleStore):
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> tuple[LearnedAllowRule, ...]:
        raw = read_document(self._path).get(_RULES, [])
        if not isinstance(raw, list):
            return ()
        loaded: list[LearnedAllowRule] = []
        for item in raw:
            rule = _rule_of(item)
            # 读不动的条目直接跳过而不是抛错: 一条格式坏掉的规则不该让整个会话起不来,
            # 而"少一条 Allow 规则"的后果是多问一次, 方向是安全的.
            if rule is not None:
                loaded.append(rule)
        return tuple(loaded)

    def save(self, rules: tuple[LearnedAllowRule, ...]) -> None:
        document = read_document(self._path)
        document[_RULES] = [_payload_of(rule) for rule in rules]
        write_document(self._path, document)


def _payload_of(rule: LearnedAllowRule) -> dict[str, Any]:
    body: dict[str, Any] = {
        "rule_id": rule.rule_id,
        "scope": rule.scope.value,
        "created_at_epoch": rule.created_at_epoch,
        "session_id": rule.session_id,
        "revoked": rule.revoked,
        "label": rule.label,
        "mode": rule.match.mode.value,
    }
    if rule.expires_at_epoch is not None:
        body["expires_at_epoch"] = rule.expires_at_epoch
    if rule.match.script_content_hash is not None:
        body["script_content_hash"] = rule.match.script_content_hash
    for name in _MATCH_FIELDS:
        body[name] = getattr(rule.match, name)
    return body


def _rule_of(item: object) -> LearnedAllowRule | None:
    if not isinstance(item, dict):
        return None
    try:
        match = RuleMatch(
            mode=SessionMode.from_value(str(item["mode"])),
            script_content_hash=(
                str(item["script_content_hash"])
                if item.get("script_content_hash")
                else None
            ),
            **{name: str(item[name]) for name in _MATCH_FIELDS},
        )
        return LearnedAllowRule(
            rule_id=str(item["rule_id"]),
            scope=ApprovalScope(str(item["scope"])),
            match=match,
            created_at_epoch=float(item["created_at_epoch"]),
            session_id=str(item.get("session_id", "")),
            expires_at_epoch=(
                float(item["expires_at_epoch"])
                if item.get("expires_at_epoch") is not None
                else None
            ),
            revoked=bool(item.get("revoked", False)),
            label=str(item.get("label", "")),
        )
    except (KeyError, ValueError, TypeError):
        return None
