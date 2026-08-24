"""渐进式授权与学习式 Allow 规则 (ADR-0013 §5.1).

`always` **不是无条件放行**, 而是创建一条受限的结构化 Allow 规则. 它至少绑定命令指纹,
可执行文件身份, 目标集合, 工作区身份, 模式, 策略版本和执行画像; 任何一项变化, 规则就
不再命中.

四条不能创建学习规则的情形, 写在 ``can_learn`` 里:

- Hard Deny 与 Mandatory Ask —— 前者不能被覆盖, 后者按定义要逐次批准.
- 受保护路径与提权.
- 目标集合未封闭 (DYNAMIC / UNKNOWN) —— 规则会匹配到一个当时并不存在的目标.
- 任意网络与下载后执行.

"允许所有 Python" 这类宽泛规则不是合法的渐进式授权: 它绑定不到任何具体事实, 因此
``LearnedAllowRule`` 的构造本身就要求那些字段非空.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from forgecli.domain.intents import SessionMode
from forgecli.domain.security.decision import AuthorizationDecision
from forgecli.domain.security.vocabulary import ApprovalScope, Decision, DecisionReason
from forgecli.domain.tool.hashing import digest
from forgecli.domain.tool.plan import ToolPlan

__all__ = ["LearnedAllowRule", "RuleMatch", "RuleSet", "can_learn"]


@dataclass(frozen=True)
class RuleMatch:
    """一次待匹配的事实快照. 字段与规则一一对应, 缺一不可."""

    tool_name: str
    plan_hash: str
    command_hash: str
    executable_identity_hash: str
    target_set_hash: str
    workspace_id: str
    mode: SessionMode
    policy_version: str
    execution_profile_hash: str
    script_content_hash: str | None = None


@dataclass(frozen=True)
class LearnedAllowRule:
    """人类明确选择后创建的受限 Allow 规则. LLM 不得创建, 扩大或选择它."""

    rule_id: str
    scope: ApprovalScope
    match: RuleMatch
    created_at_epoch: float
    session_id: str
    expires_at_epoch: float | None = None
    revoked: bool = False
    # 给人看的标签 (可执行文件基名), 不参与 matches, 也不进 rule_hash. 没有它,
    # /rules 就只能列出一串 rule_id, 用户无从判断该撤销哪一条.
    label: str = ""

    def __post_init__(self) -> None:
        if self.scope is ApprovalScope.ONCE:
            raise ValueError("once 不产生学习规则")
        for name in (
            "tool_name",
            "command_hash",
            "executable_identity_hash",
            "workspace_id",
            "execution_profile_hash",
        ):
            if not getattr(self.match, name):
                raise ValueError(f"学习规则必须绑定 {name}, 不能泛化")

    @property
    def rule_hash(self) -> str:
        """规则身份. 只由 scope 与匹配事实决定 —— label 变了不代表规则变了."""
        return digest({"scope": self.scope, "match": self.match})

    def active_at(self, now_epoch: float) -> bool:
        if self.revoked:
            return False
        return self.expires_at_epoch is None or now_epoch < self.expires_at_epoch

    def matches(self, candidate: RuleMatch) -> bool:
        """逐项比对. 任何一项不同都不命中 —— 这正是"不泛化"的实现方式."""
        if self.scope is ApprovalScope.WORKSPACE and (
            candidate.workspace_id != self.match.workspace_id
        ):
            return False
        return candidate == self.match

    def invalidated_by(
        self, *, policy_version: str, execution_profile_hash: str
    ) -> tuple[str, ...]:
        """当前环境下这条规则已经不可能命中的原因. 空元组表示仍然有效.

        规则在环境变化时集体失效是设计意图 (ADR-0013 §5.1), 但用户感受到的是"我明明
        批准过". 把原因摆出来, 让"为什么又问我"这件事可解释.
        """
        reasons: list[str] = []
        if self.match.policy_version != policy_version:
            reasons.append("策略版本已变")
        if self.match.execution_profile_hash != execution_profile_hash:
            reasons.append("执行环境已变 (PATH / 环境净化 / 受保护路径)")
        return tuple(reasons)

    def revoke(self) -> LearnedAllowRule:
        return replace(self, revoked=True)


class RuleSet:
    """进程内的学习规则集合. 支持列出, 撤销, 过期与命中审计."""

    def __init__(self, rules: tuple[LearnedAllowRule, ...] = ()) -> None:
        self._rules: list[LearnedAllowRule] = list(rules)

    @property
    def rules(self) -> tuple[LearnedAllowRule, ...]:
        return tuple(self._rules)

    def add(self, rule: LearnedAllowRule) -> None:
        self._rules.append(rule)

    def find(
        self, candidate: RuleMatch, *, session_id: str, now_epoch: float
    ) -> LearnedAllowRule | None:
        return next(
            (
                rule
                for rule in self._rules
                if rule.active_at(now_epoch) and rule.matches(candidate)
            ),
            None,
        )

    def revoke(self, rule_id: str) -> bool:
        for index, rule in enumerate(self._rules):
            if rule.rule_id == rule_id and not rule.revoked:
                self._rules[index] = rule.revoke()
                return True
        return False

    def prune(self, now_epoch: float) -> int:
        """清掉已过期或已撤销的规则, 返回清理条数."""
        before = len(self._rules)
        self._rules = [rule for rule in self._rules if rule.active_at(now_epoch)]
        return before - len(self._rules)


# 这些理由下不允许创建学习规则.
#
# 后三条是"分析没做完"或"分析说别自动跑", 沉淀成规则等于把一次退让变成永久放行:
#   SCRIPT_EXECUTION       分类器明确说了不能自动执行, 或脚本里有看不透的构造.
#   CLASSIFIER_UNAVAILABLE 分类器超时/未配置. 学下来等于"以后分类器坏掉就自动放行".
#   CLASSIFIER_LOW_CONFIDENCE 同上, 只是失败形式不同.
#   PARSE_INCOMPLETE       连命令结构都没解析完整, 规则绑不到可靠事实.
_UNLEARNABLE_REASONS = frozenset(
    {
        DecisionReason.EXTERNAL_IRREVERSIBLE_EFFECT,
        DecisionReason.HARD_DENY_DESTRUCTIVE,
        DecisionReason.HARD_DENY_PRIVILEGE_ESCALATION,
        DecisionReason.HARD_DENY_PROTECTED_PATH,
        DecisionReason.HARD_DENY_CREDENTIAL_ACCESS,
        DecisionReason.HARD_DENY_REMOTE_CODE_EXECUTION,
        DecisionReason.HARD_DENY_SCRIPT_SIGNAL,
        DecisionReason.UNRESOLVED_TARGET_SET,
        DecisionReason.UNEVALUATED_CAPABILITY,
        DecisionReason.SCRIPT_EXECUTION,
        DecisionReason.CLASSIFIER_UNAVAILABLE,
        DecisionReason.CLASSIFIER_LOW_CONFIDENCE,
        DecisionReason.PARSE_INCOMPLETE,
    }
)


def can_learn(
    decision: AuthorizationDecision, scope: ApprovalScope
) -> tuple[bool, str]:
    """这次批准能不能沉淀成规则. 返回 (可以吗, 不可以的理由)."""
    if scope is ApprovalScope.ONCE:
        return False, "once 只对本次请求有效"
    if decision.mandatory:
        return False, "Mandatory Ask 只能逐次批准"
    if decision.decision is Decision.DENY:
        return False, "被拒绝的请求不能产生 Allow 规则"
    if decision.reason in _UNLEARNABLE_REASONS:
        return False, f"{decision.reason.value} 不允许沉淀为规则"
    if not _closed_targets(decision.effective_plan):
        return False, "目标集合未封闭, 规则会匹配到当时并不存在的目标"
    if not decision.executable_identity_hash:
        # 只有 shell 分析器会给出身份. 其余工具 (fs.*, git.read 等) 因此学不到规则 ——
        # 这不是遗漏: 它们的 plan_hash 里含 normalized_input, 对 fs.apply_patch 就是
        # 替换后的完整文件内容, 规则只会在下次写入逐字节相同的内容时命中, 毫无用处.
        return False, "拿不到可执行文件身份, 规则绑不住"
    return True, ""


def _closed_targets(plan: ToolPlan) -> bool:
    return plan.target_resolution.closed
