"""安全裁决的基础词汇 (ADR-0013 §4).

优先级是这套词汇的核心, 唯一的实现在 PolicyEngine.decide 的分支顺序里:

    Hard Deny > Mandatory Ask > Ask > Allow

这里刻意**不**再放第二份优先级编码 (曾经有个 RuleKind 枚举带 priority 映射, 零消费方).
同一套优先级存两份必然漂移, 而漂移的那一份不会报错.

Hard Deny 是任何模式, 普通用户确认和风险分类器都不能覆盖的底线; Mandatory Ask 可以由
人类逐次批准, 但不能被缓存, 历史批准, 学习规则或模式满足.
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "POLICY_VERSION",
    "ApprovalScope",
    "Decision",
    "DecisionReason",
]

# 本地策略版本. 规则表, 优先级, mode 矩阵或 ApprovalBinding 字段集变化时递增,
# 使缓存与学习规则失效.
#
# 3: ADR-0028 收缩 ApprovalBinding 并合并审批视图. 旧规则绑的是旧口径的字段集,
#    沿用等于拿旧绑定去满足新裁决.
# 4: ADR-0030 用围栏边界顶替模式能力预算. 旧规则绑的是"某模式允许某能力"这个口径,
#    而现在的判据是"围栏兜不兜得住", 沿用等于拿旧口径去满足新裁决.
POLICY_VERSION = "4"


class Decision(Enum):
    """最终裁决. ASK 是阻塞状态, 不是"返回错误让模型自己决定要不要重试"."""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class ApprovalScope(Enum):
    """人类确认的授权范围.

    只有两档. ADR-0013 §5.1 还写了 session 与 always, 但默认 HITL 不产出它们, 也没有
    别的界面能产出 —— 枚举里留着两个永远为空的取值, 只会让读代码的人以为某处支持它们.
    """

    ONCE = "once"
    WORKSPACE = "workspace"

    @property
    def learned(self) -> bool:
        """是否会留下一条可复用的规则 (ONCE 不留)."""
        return self is not ApprovalScope.ONCE


class DecisionReason(Enum):
    """裁决理由码. 进 observation, 审计和测试断言, 取值不可改."""

    # -- ALLOW --
    PLAN_ONLY_FAST_PATH = "plan_only_fast_path"
    ARTIFACT_READ_FAST_PATH = "artifact_read_fast_path"
    MEMORY_WRITE_FAST_PATH = "memory_write_fast_path"
    WORKSPACE_READ_FAST_PATH = "workspace_read_fast_path"
    # 围栏把这次执行关住了 (ADR-0030). 与上一条分开是为了让审计能回答"这次为什么没
    # 问人": 一个是工具自己就窄, 一个是内核拦住了它伸出去的路.
    FENCE_CONFINED = "fence_confined"
    RULE_ALLOW = "rule_allow"
    LEARNED_ALLOW = "learned_allow"
    # 人类批准且全量重验通过后, 普通 ASK 转成的一次性 ALLOW.
    APPROVAL_GRANTED = "approval_granted"

    # -- ASK --
    MODE_REQUIRES_APPROVAL = "mode_requires_approval"
    EXTERNAL_IRREVERSIBLE_EFFECT = "external_irreversible_effect"
    UNRESOLVED_TARGET_SET = "unresolved_target_set"
    SCRIPT_EXECUTION = "script_execution"
    CLASSIFIER_UNAVAILABLE = "classifier_unavailable"
    CLASSIFIER_LOW_CONFIDENCE = "classifier_low_confidence"
    PARSE_INCOMPLETE = "parse_incomplete"
    OUTSIDE_WORKSPACE = "outside_workspace"
    # 命令解析成功, 但这个可执行文件的影响范围推导不出来 (`java -jar x.jar` 会碰什么?).
    # 与 PARSE_INCOMPLETE 分开: 那是"没读懂命令", 这是"读懂了但不认识这个程序".
    UNPROVEN_EXECUTABLE = "unproven_executable"

    # -- DENY --
    # 这条**不是**安全底线, 是"这台机器上跑不了". 单列一个码而不是复用 HARD_DENY_*:
    # 混进去会让审计里多出一堆假的安全拒绝, 而它们其实只是模型用错了平台的命令.
    EXECUTABLE_NOT_FOUND = "executable_not_found"
    SCRIPT_CONTENT_UNAVAILABLE = "script_content_unavailable"
    HARD_DENY_DESTRUCTIVE = "hard_deny_destructive"
    HARD_DENY_PRIVILEGE_ESCALATION = "hard_deny_privilege_escalation"
    HARD_DENY_PROTECTED_PATH = "hard_deny_protected_path"
    HARD_DENY_CREDENTIAL_ACCESS = "hard_deny_credential_access"
    HARD_DENY_REMOTE_CODE_EXECUTION = "hard_deny_remote_code_execution"
    HARD_DENY_SCRIPT_SIGNAL = "hard_deny_script_signal"
    RULE_DENY = "rule_deny"
    # fail closed 兜底: 分析器既无法证明安全, 也无法把请求交给人类确认时用它
    # (例如非交互环境下分类器不可用). 默认后果必须是拒绝, 不是"没有规则匹配所以放行".
    UNEVALUATED_CAPABILITY = "unevaluated_capability"
    RECOVERY_UNAVAILABLE = "recovery_unavailable"
