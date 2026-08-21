"""哪些能力可以不问人 —— 由围栏边界决定, 不由模式表决定 (ADR-0030 决策 4).

顶替了原先的 `modes.py` 能力预算表. 差别在**判据的来源**:

    预算表:  这次调用请求了哪些 Capability, 该模式的表里有没有
    围栏:    这次调用要触达的东西, 围栏兜不兜得住

两者形状相似 (都是"表外一律 ASK"), 但前者要靠分析器从命令串里推导出一个准确的能力
集合才成立, 后者不需要 —— 围栏在系统调用那一刻说了算, 分析器只是在描述它.

三个能力仍然在任何情况下都不自动放行, 理由与围栏无关:

    CREDENTIAL_ACCESS             读凭证永远需要人类在场.
    EXTERNAL_IRREVERSIBLE_EFFECT  ADR-0013 §4.1 要求逐次批准.
    UNKNOWN                       无法自证的能力走最保守路径.

EXTERNAL_READ / EXTERNAL_WRITE 同样不进自动集合, 而且**含 full_access**. 这是相对
旧预算表的一处收紧: `full_access` 的语义是放开网络, 不是放开宿主文件系统.
围栏对工作区外一律只读 allowlist, 要读 allowlist 之外的东西就得人类点头.
"""

from __future__ import annotations

from forgecli.domain.execution.fence import FencePolicy
from forgecli.domain.tool.capability import Capability

__all__ = ["capabilities_requiring_approval", "fence_allowed_capabilities"]

# 与围栏无关, 任何情况下都要问人.
_NEVER_AUTO = frozenset(
    {
        Capability.CREDENTIAL_ACCESS,
        Capability.EXTERNAL_IRREVERSIBLE_EFFECT,
        Capability.EXTERNAL_READ,
        Capability.EXTERNAL_WRITE,
        Capability.UNKNOWN,
    }
)

# 不需要围栏就能自动放行的: 读工作区, 写计划, 起一个已被裁决绑定的子进程.
_ALWAYS = frozenset(
    {
        Capability.PLAN_ONLY,
        Capability.WORKSPACE_READ,
        Capability.SPAWN_PROCESS,
    }
)

# 工作区内的改动. 可写围栏 + 写时复制快照兜底, 所以不需要逐次确认.
_WORKSPACE_MUTATION = frozenset(
    {
        Capability.WORKSPACE_WRITE,
        Capability.WORKSPACE_DELETE,
        Capability.PATH_MOVE,
    }
)

# 只有围栏真的立起来了才自动放行的.
#
# 这是本模块的核心: 旧预算表把 EXECUTE_SHELL 放进 auto 档, 靠的是分析器证明这条命令
# 不会伸出去; 现在靠的是围栏让它伸不出去. 没有围栏 (UNCONFINED) 时这一组落回 ASK,
# 而不是退回去证明 —— ADR-0030 决策 5.
_NEEDS_FENCE = frozenset(
    {
        Capability.EXECUTE_SHELL,
        Capability.EXECUTE_SCRIPT,
        Capability.MODEL_CALL,
    }
)


def fence_allowed_capabilities(
    fence: FencePolicy | None, *, confined: bool
) -> frozenset[Capability]:
    """这道围栏下可以自动放行的能力集合."""
    allowed = set(_ALWAYS)
    if fence is None:
        return frozenset(allowed)
    if not fence.read_only:
        allowed |= _WORKSPACE_MUTATION
    if confined:
        allowed |= _NEEDS_FENCE
        if fence.network_allowed:
            allowed.add(Capability.NETWORK_ACCESS)
    return frozenset(allowed - _NEVER_AUTO)


def capabilities_requiring_approval(
    capabilities: frozenset[Capability],
    fence: FencePolicy | None,
    *,
    confined: bool,
) -> frozenset[Capability]:
    """本次调用中围栏兜不住, 因而需要人类确认的能力."""
    return frozenset(
        capabilities - fence_allowed_capabilities(fence, confined=confined)
    )
