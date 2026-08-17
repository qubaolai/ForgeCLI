"""四种 mode 的能力预算 (ADR-0013 §9).

模式定义"当前请求可以自动使用哪些能力", 不定义安全底线: 它**不能取消 Hard Deny**, 也
不能满足 Mandatory Ask. 因此这里只有一张"自动允许"的表, 表外的能力一律落 ASK, 而不是
一张 allow/deny 双向表 —— 后者会让人以为把某个能力写进 deny 列才叫拦住了它.

能力落在表外只意味着"需要人类确认", 真正的拒绝来自 Hard Deny, 受保护路径和规则引擎.
"""

from __future__ import annotations

from forgecli.domain.intents import SessionMode
from forgecli.domain.tool.capability import Capability

__all__ = ["auto_allowed_capabilities", "capabilities_requiring_approval"]

# plan: PlanTool 与普通只读工具.
#
# SPAWN_PROCESS 在最低档就放行, 是因为 ADR-0013 §1 明确把 GitReadTool 列进 plan 档的
# 工具目录, 而它靠子进程读 git. 真正危险的子进程一定同时声明 EXECUTE_SHELL 或
# EXECUTE_SCRIPT, 那两个不在这一档.
_PLAN = frozenset(
    {
        Capability.PLAN_ONLY,
        Capability.WORKSPACE_READ,
        Capability.SPAWN_PROCESS,
    }
)

# accept_edits: 工作区内普通编辑. Shell, 脚本, 网络和工作区外操作仍需询问.
_ACCEPT_EDITS = _PLAN | {
    Capability.WORKSPACE_WRITE,
    Capability.WORKSPACE_DELETE,
    Capability.PATH_MOVE,
}

# auto: 受控工作区命令和满足环境条件的脚本.
#
# EXECUTE_SHELL / EXECUTE_SCRIPT 进这一档只表示"模式预算允许", 不表示可以直接跑:
# 无沙箱环境下它们仍要经 ScriptAnalyzer 与 Background Safety Classifier, 由分析器产出
# 的 requires_ask 把不确定的请求拉回 ASK (ADR-0013 §10).
_AUTO = _ACCEPT_EDITS | {
    Capability.EXECUTE_SHELL,
    Capability.EXECUTE_SCRIPT,
    Capability.MODEL_CALL,
}

# full_access: 更广泛的本地与网络能力.
#
# 三个能力在任何模式下都不进自动集合:
#   CREDENTIAL_ACCESS            读凭证永远需要人类在场.
#   EXTERNAL_IRREVERSIBLE_EFFECT ADR-0013 §4.1 要求逐次 Mandatory Ask, 含 full_access.
#   UNKNOWN                      无法自证的能力默认走最保守路径.
_FULL_ACCESS = _AUTO | {
    Capability.NETWORK_ACCESS,
    Capability.EXTERNAL_READ,
    Capability.EXTERNAL_WRITE,
}

_BUDGETS: dict[SessionMode, frozenset[Capability]] = {
    SessionMode.PLAN: _PLAN,
    SessionMode.ACCEPT_EDITS: _ACCEPT_EDITS,
    SessionMode.AUTO: _AUTO,
    SessionMode.FULL_ACCESS: _FULL_ACCESS,
}


def auto_allowed_capabilities(mode: SessionMode) -> frozenset[Capability]:
    """该模式下可以自动允许的能力集合."""
    return _BUDGETS[mode]


def capabilities_requiring_approval(
    mode: SessionMode, capabilities: frozenset[Capability]
) -> frozenset[Capability]:
    """本次调用中超出模式预算, 因而需要人类确认的能力."""
    return frozenset(capabilities - auto_allowed_capabilities(mode))
