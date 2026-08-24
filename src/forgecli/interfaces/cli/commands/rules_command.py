"""/rules: 管理 `always` 沉淀下来的学习规则 (ADR-0013 §5.1).

    /rules                    列出本工作区的规则
    /rules --revoke <id>      撤销一条
    /rules --prune            清掉已撤销与已过期的条目

ADR-0013 §5.1 要求学习规则"必须支持列出, 撤销, 过期和审计". 前两条在这里, 过期由
LearnedAllowRule.active_at 判定, 审计走裁决事件 —— 但没有这个命令, 用户只能创建规则,
不能回头看自己批准过什么. 一个只进不出的授权面本身就是安全问题.

**只能由人发起.** 它不在工具目录里, LLM 请求不到 —— 能自己撤销规则的 Agent 也能自己
创建规则.

失效原因单独标出来: 策略版本或执行环境变了以后, 旧规则会集体失效, 用户看到的现象是
"我明明批准过, 怎么又问我". 把原因写在列表里, 这件事才是可解释的.
"""

from __future__ import annotations

from datetime import datetime

from forgecli.application.interaction_ports import UserOutput
from forgecli.application.security.learned_rules import LearnedRuleService
from forgecli.application.slash_commands.base import CommandHandler
from forgecli.domain.execution.profile import ExecutionProfile
from forgecli.domain.intents import SlashCommand
from forgecli.domain.security.rules import LearnedAllowRule
from forgecli.domain.security.vocabulary import POLICY_VERSION

__all__ = ["RulesCommand"]


class RulesCommand(CommandHandler):
    def __init__(
        self,
        service: LearnedRuleService,
        profile: ExecutionProfile,
        workspace_id: str,
        output: UserOutput,
    ) -> None:
        self._service = service
        self._profile = profile
        self._workspace_id = workspace_id
        self._output = output

    def execute(self, command: SlashCommand) -> bool:
        args = command.args
        if "--revoke" in args:
            return self._revoke(_positional(args))
        if "--prune" in args:
            return self._prune()
        return self._list()

    # ---- 子命令 ----

    def _list(self) -> bool:
        rules = tuple(
            rule
            for rule in self._service.rules
            if rule.match.workspace_id == self._workspace_id
        )
        if not rules:
            self._output.print(
                "本工作区没有学习规则. 审批时选 always 会在这里留下一条."
            )
            return False
        for rule in rules:
            self._output.print(self._row(rule))
        self._output.print(f"\n共 {len(rules)} 条 · 撤销用 /rules --revoke <rule_id>")
        return False

    def _revoke(self, wanted: tuple[str, ...]) -> bool:
        if not wanted:
            self._output.print("用法: /rules --revoke <rule_id>")
            return False
        if not self._service.revoke(wanted[0]):
            self._output.print(f"找不到未撤销的规则: {wanted[0]}")
            return False
        self._output.print(f"已撤销 {wanted[0]}. 下次遇到相同命令会重新询问.")
        return True

    def _prune(self) -> bool:
        removed = self._service.prune()
        self._output.print(f"清理了 {removed} 条已撤销 / 已过期的规则.")
        return removed > 0

    # ---- 渲染 ----

    def _row(self, rule: LearnedAllowRule) -> str:
        created = datetime.fromtimestamp(rule.created_at_epoch).strftime(
            "%Y-%m-%d %H:%M"
        )
        label = rule.label or rule.match.tool_name
        line = (
            f"{rule.rule_id}  {created}  {rule.scope.value:<9}"
            f"  {rule.match.mode.value:<12}  {label}"
        )
        return f"{line}\n    {self._state(rule)}"

    def _state(self, rule: LearnedAllowRule) -> str:
        if rule.revoked:
            return "已撤销"
        stale = rule.invalidated_by(
            policy_version=POLICY_VERSION,
            execution_profile_hash=self._profile.execution_profile_hash,
        )
        if stale:
            return "已失效: " + ", ".join(stale) + " (下次会重新询问)"
        return "生效中"


def _positional(args: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(arg for arg in args if not arg.startswith("--"))
