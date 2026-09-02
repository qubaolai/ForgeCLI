"""渐进式授权: 把一次 `always` 批准沉淀成一条受限 Allow 规则 (ADR-0013 §5.1).

三条边界, 每条都对应一种"看起来方便但实际上是后门"的做法:

1. **只有 `shell_run` 能学.** 规则要绑定可执行文件身份, 而只有 shell 分析器解析得出它.
   其余工具的 `plan_hash` 里含 normalized_input —— 对 `fs_apply_patch` 那就是替换后的
   完整文件内容, 规则只会在下次写入逐字节相同内容时命中, 给了也没用.
2. **命中规则不跳过 Hard Deny.** 查规则发生在策略引擎出结论**之后**, 且只把 ASK 抬成
   ALLOW. DENY 永远到不了这一步, 所以"先学一条规则再触发 Hard Deny"这条路不存在.
3. **LLM 不能创建规则.** 创建的唯一入口是人在审批界面选了 always, 由协调器调用.
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable

from forgecli.domain.security.context import PolicyContext
from forgecli.domain.security.decision import AuthorizationDecision
from forgecli.domain.security.rules import (
    LearnedAllowRule,
    RuleMatch,
    RuleSet,
    can_learn,
)
from forgecli.domain.security.vocabulary import ApprovalScope
from forgecli.domain.tool.hashing import digest, digest_text
from forgecli.domain.tool.plan import ShellSubject, ToolPlan

__all__ = ["LearnedRuleService", "LearnedRuleStore"]


class LearnedRuleStore(ABC):
    """学习规则的持久化出口. 规则跨会话有效, 所以必须落盘."""

    @abstractmethod
    def load(self) -> tuple[LearnedAllowRule, ...]: ...

    @abstractmethod
    def save(self, rules: tuple[LearnedAllowRule, ...]) -> None: ...


def _new_rule_id() -> str:
    return f"rule_{uuid.uuid4().hex[:12]}"


class LearnedRuleService:
    """规则的创建, 查询与撤销. 协调器与授权服务共用一份."""

    def __init__(
        self,
        store: LearnedRuleStore,
        *,
        workspace_id: str = "workspace",
        clock: Callable[[], float] = time.time,
        rule_id_factory: Callable[[], str] = _new_rule_id,
    ) -> None:
        self._store = store
        self._workspace_id = workspace_id
        self._clock = clock
        self._new_rule_id = rule_id_factory
        self._rules = RuleSet(self._store.load())

    @property
    def rules(self) -> tuple[LearnedAllowRule, ...]:
        return self._rules.rules

    def learnable(self, decision: AuthorizationDecision) -> bool:
        """这次批准能不能给用户"always"这个选项."""
        allowed, _ = can_learn(decision, ApprovalScope.WORKSPACE)
        return allowed

    def block_reason(self, decision: AuthorizationDecision) -> str:
        """不能给"always"时的那条理由; 能给时返回空串.

        判据与 learnable 同源 —— 同一次 can_learn 的两半. 分成两个方法而不是让
        learnable 返回二元组: 调用方多数只关心能不能, 而理由只有审批界面要.
        """
        allowed, reason = can_learn(decision, ApprovalScope.WORKSPACE)
        return "" if allowed else reason

    def find(
        self, decision: AuthorizationDecision, policy: PolicyContext
    ) -> LearnedAllowRule | None:
        """查一条命中的规则. 拿不到身份 (非 shell 工具) 时直接返回 None."""
        if not decision.executable_identity_hash:
            return None
        return self._rules.find(
            self._match_of(decision, policy),
            session_id=policy.session_id,
            now_epoch=self._clock(),
        )

    def record(
        self,
        decision: AuthorizationDecision,
        policy: PolicyContext,
        scope: ApprovalScope,
    ) -> LearnedAllowRule | None:
        """把一次人类批准沉淀成规则. 不可学时返回 None, 不抛错.

        不抛错是有意的: 用户选了 always 但这次请求恰好不可学 (比如 Mandatory Ask),
        本次执行仍然应该按 once 放行, 只是学不到东西.
        """
        allowed, _ = can_learn(decision, scope)
        if not allowed:
            return None
        rule = LearnedAllowRule(
            rule_id=self._new_rule_id(),
            scope=scope,
            match=self._match_of(decision, policy),
            created_at_epoch=self._clock(),
            session_id=policy.session_id,
            label=_label_of(decision),
        )
        self._rules.add(rule)
        self._store.save(self._rules.rules)
        return rule

    def revoke(self, rule_id: str) -> bool:
        if not self._rules.revoke(rule_id):
            return False
        self._store.save(self._rules.rules)
        return True

    def prune(self) -> int:
        """清掉已撤销与已过期的规则, 返回清理条数. 只由用户经 /rules 发起."""
        removed = self._rules.prune(self._clock())
        if removed:
            self._store.save(self._rules.rules)
        return removed

    def _match_of(
        self, decision: AuthorizationDecision, policy: PolicyContext
    ) -> RuleMatch:
        plan = decision.effective_plan
        return RuleMatch(
            tool_name=plan.tool_name,
            plan_hash=plan.plan_hash,
            command_hash=_command_hash(plan),
            executable_identity_hash=decision.executable_identity_hash,
            target_set_hash=plan.target_set_hash,
            workspace_id=policy.workspace_id or self._workspace_id,
            mode=policy.mode,
            policy_version=policy.policy_version,
            execution_profile_hash=policy.execution_profile_hash,
            script_content_hash=_script_hash(decision),
        )


def _label_of(decision: AuthorizationDecision) -> str:
    """规则的展示标签: 可执行文件基名, 去重保序, 至多前三个.

    只用基名是有意的. 完整命令里可能有凭证与主机名, 而 `git`, `npm` 这样的基名带不出
    任何东西, 又刚好是用户在 /rules 里唯一认得出的信息. 截到三个是因为再长也认不清,
    条数本身由 rule_id 与创建时间区分.
    """
    names = tuple(dict.fromkeys(decision.executable_names))
    if not names:
        return decision.effective_plan.tool_name
    shown = " | ".join(names[:3])
    return f"{shown} …" if len(names) > 3 else shown


def _command_hash(plan: ToolPlan) -> str:
    subject = plan.analysis_subject
    if isinstance(subject, ShellSubject):
        return digest_text(subject.raw_command)
    return digest_text(plan.tool_name)


def _script_hash(decision: AuthorizationDecision) -> str | None:
    """脚本内容的哈希. 改一个字符就换一条规则 —— 这正是"不泛化"的意思.

    取的是**分析实际读过的那几份正文**, 而不是 analysis_subject. 早先只认
    `ScriptSubject`, 而没有任何工具产出 ScriptSubject —— 于是 shell 路径的脚本一律拿不到
    内容哈希, `bash deploy.sh` 学到的规则只绑住命令行那一串字, deploy.sh 随后改成什么
    都照样命中. 一条永久放行 + 一个可自由改写的文件, 正好是"批准过的脚本"这种类别式
    授权最危险的形态.

    多份正文 (`a.sh && b.sh`) 合成一个哈希: 任一份变了都换一条规则.
    """
    snapshots = decision.script_snapshots
    if not snapshots:
        return None
    return digest(
        tuple(
            (snapshot.origin, snapshot.path or "", snapshot.content_hash)
            for snapshot in snapshots
        )
    )
