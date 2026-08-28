"""哪些能力可以不问人 —— 由围栏边界决定, 不由模式表决定 (ADR-0030 决策 4).

顶替了原先的 `modes.py` 能力预算表. 差别在**判据的来源**:

    预算表:  这次调用请求了哪些 Capability, 该模式的表里有没有
    围栏:    这次调用要触达的东西, 围栏兜不兜得住

两者形状相似 (都是"表外一律 ASK"), 但前者要靠分析器从命令串里推导出一个准确的能力
集合才成立, 后者不需要 —— 围栏在系统调用那一刻说了算, 分析器只是在描述它.

五个能力在**围栏之外**另设一道闸, 理由与围栏无关:

    CREDENTIAL_ACCESS              读凭证要人类在场.
    EXTERNAL_IRREVERSIBLE_EFFECT   ADR-0013 §4.1 要求逐次批准.
    EXTERNAL_READ / EXTERNAL_WRITE 工作区之外的宿主文件系统.
    UNKNOWN                        无法自证的能力走最保守路径.

## full_access 不受这道闸约束 (2026-08-28, ADR-0030 决策 4 修订)

`fence.unrestricted` 为真且围栏**确实立起来了**时, 上面五个也自动放行. 那一档的语义就是
"只剩红线兜底" —— `/mode` 的说明一直是这么写的, 而这个模块此前并没有兑现它.

这不是把边界拆了, 是把边界**收归一处**: 工作区外写入与受保护路径读取仍然被围栏在系统
调用那一刻拦下, 凭证外泄仍然是 Hard Deny 而不是 ASK (见 workspace_analyzer). 变的只是
"围栏拦不住的那些要不要先问一次人", full_access 的回答是不问.

**UNCONFINED 时这条不生效.** 没有围栏就没有"在边界内执行"这回事, 那一档退回逐项确认.
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

# 不需要围栏就能自动放行的: 读工作区, 写计划, 读回自己落盘的输出, 起一个已被裁决
# 绑定的子进程.
#
# ARTIFACT_READ 在这里而不是走 EXTERNAL_READ: 它读的确实是工作区之外的路径, 但那个
# 路径不由模型指定 —— 入参是一个十六进制内容哈希, 校验过才拼得出文件名. 按
# EXTERNAL_READ 算的话, 每次取回一段被降级掉的历史输出都要问一次人, 而那正是
# ADR-0032 一级降级要省下的东西.
#
# MEMORY_WRITE 同理, 而且**静默正是它的设计目标** (ADR-0033 决策 2): 记忆的全部价值
# 在于无感积累, 一个每次都要人点确认的记忆系统, 用户会在第三次的时候关掉它. 承重的
# 缓解不是这道闸, 是决策 3 那条"记忆不参与任何安全裁决".
_ALWAYS = frozenset(
    {
        Capability.PLAN_ONLY,
        Capability.ARTIFACT_READ,
        Capability.MEMORY_WRITE,
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

# 只有围栏真的立起来了才自动放行的模型调用. 它不是通用子进程, 也不受
# accept_edits / auto 的 Shell 自主性边界影响.
_NEEDS_FENCE = frozenset({Capability.MODEL_CALL})

# 只有模式明确允许自动 Shell, 且围栏真的立起来了, 才自动放行的能力.
#
# accept_edits 与 auto 的文件边界相同, 自主性边界不同: 前者可以自动调用专用文件工具,
# 但任何通用 Shell 都要问人; 后者才允许 Shell 在围栏内自动执行. 没有围栏
# (UNCONFINED) 时两档都落回 ASK, 而不是退回去证明 —— ADR-0030 决策 5.
#
# NETWORK_ACCESS 在这一组里, 而**不是**"围栏放开网络时才自动放行" (ADR-0040 §8.3 的
# C 类处置, 2026-08-28):
#
# 批准一次 NETWORK_ACCESS 并不会让围栏放宽 —— 围栏由 mode 编译 (dispatcher 的
# `fence_factory(mode)`), 授权信封里没有它. 于是在 auto 档问"要不要联网", 用户点同意
# 之后命令照样被围栏拦下. 那是一个答案不起作用的问题.
#
# 网络到底通不通由 `fence.network_allowed` 在系统调用那一刻说了算. 围栏立起来了就不必
# 问; 没立起来时, shell_run 必然声明 EXECUTE_SHELL, 这一组本来就落回 ASK.
#
# 于是 `NETWORK_TOOLS` 那张命令名表退出授权路径, 只剩两个用处: 生成给人看的风险摘要
# (`PlanEffects.network_targets`), 以及 Hard Deny 的 `curl | sh` 形状判定. 表里漏一条
# 网络工具, 后果从"少一次审批"变成"风险摘要少一行".
_AUTOMATIC_SHELL = frozenset(
    {
        Capability.EXECUTE_SHELL,
        Capability.EXECUTE_SCRIPT,
        Capability.NETWORK_ACCESS,
    }
)


def fence_allowed_capabilities(
    fence: FencePolicy | None, *, confined: bool
) -> frozenset[Capability]:
    """这道围栏下可以自动放行的能力集合."""
    allowed = set(_ALWAYS)
    if fence is None:
        return frozenset(allowed)
    if fence.unrestricted and confined:
        # full_access + 真围栏: 边界只剩围栏本身与 Hard Deny.
        return frozenset(Capability)
    if not fence.read_only:
        allowed |= _WORKSPACE_MUTATION
    if confined:
        allowed |= _NEEDS_FENCE
        if fence.automatic_shell:
            allowed |= _AUTOMATIC_SHELL
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
