"""回合内内容去重与文件变更通知 (ADR-0032 决策 3 / 4).

两件事同一份判据, 所以同一个 pass: 走一遍 transcript, 把每条工具结果按它自己记下的
``(source_path, source_state)`` 与前面同路径的那一条比一比.

- 状态相同 -> 后一条是重复的, 换成一行引用. **省的是 token 不是 IO**: 文件照读,
  stat 照做, 省掉的是把同一份 16 KiB 再塞一遍进上下文.
- 状态不同 -> 前一条已经过时. 这里只把它从锚点位置上摘下来, **不改写它**.

## 为什么变更通知不再原地改写 (2026-09-01 修订)

原先这一条会把前面那次读改写成一行 stale 占位. 语义没问题, 代价是致命的: 在前缀缓存
下, 改写 transcript 中段会让它之后的**全部内容**重新计费.

实测: 一次 `fs_apply_patch` 之后, 输入 17,154 里有 7,298 未命中缓存, 占那一轮未缓存
输入的 20%; 改文件密集的那一轮里三次改写合计 79,321, 占 57%. 而省下来的不过是把一份
4k 的正文换成 40 token 的占位 —— 拿 4k 的节省换 25k 从缓存价变成全价, 净亏五到十倍.

所以那套压缩机制的正确定位是**溢出保护**, 不是成本控制. 窗口没满时动它只会更贵.

通知本身仍然要发, 只是改成**追加到写入那条结果的正文末尾** (`changed_notice`, 由循环
在回填工具结果时调一次). 那个位置在尾部, 天生只发生一次, 不需要任何幂等技巧, 而且
缓存代价为零 —— 尾部本来就是新内容要去的地方.

## 为什么这个 pass 是无状态的

它不存表, 每次从 transcript 重新算. 于是"文件变没变"这个判断的两个输入, 都是各自那次
工具调用**在自己执行的那一刻**记下的 token —— 中间不管发生过什么, 后一次读必然拿到
当时的真实状态.

这一点让 ADR-0032 决策 5 (去重表注册进人工 Shell 的失效屏障) 一开始就没有必要: 要
失效的是"跨人工 Shell 复用的旧事实", 而这里根本没有旧事实可复用. 一个存起来的表才需要
那道闸; 注册一个无表可清的钩子, 正是 ADR-0028 规则 C 说的不生效的安全阀. 人工 Shell
随 ADR-0025 决策 1 修订二整体删除之后, 连那道闸本身也不存在了.

## 判据复用 path_state_token

``source_state`` 由工具调用 ``path_state_token`` 算出, 与 ADR-0027 执行前复核用的是
同一个函数. 两套判据会让安全层说"没变"而这里说"变了", 而这种分歧不会报错.
"""

from __future__ import annotations

from forgecli.application.context import placeholders
from forgecli.application.context.transcript import Slot
from forgecli.application.tools.artifact_store import ArtifactStore
from forgecli.domain.tool.result import ResultProvenance

__all__ = ["changed_notice", "plan_rewrites"]


def plan_rewrites(
    slots: tuple[Slot, ...], artifacts: ArtifactStore | None
) -> dict[tuple[int, int], str]:
    """算出这一轮 transcript 上要改写哪些块, 改成什么."""
    rewrites: dict[tuple[int, int], str] = {}
    # 每个路径当前的"代表": 后面同路径的结果都与它比.
    anchors: dict[str, Slot] = {}
    for slot in slots:
        provenance = slot.block.provenance
        if provenance is None:
            continue
        for path in provenance.mutated_paths:
            # 改过之后, 前面那次读不再代表当前状态 —— 所以它不能继续当锚点, 否则下一次
            # 读回来会被误判成"与前面相同"而换成引用.
            #
            # 只摘锚点, **不改写它**: 改写会打断前缀缓存 (见模块说明). 通知走
            # `changed_notice`, 追加在写入那条结果的尾部.
            #
            # 写入自己也不接任新锚点: 它的正文是一行改动说明, 当不了后来那次读的引用
            # 目标. 下一次真的读回来, 那一条才是新的代表.
            anchors.pop(path, None)
        if not provenance.dedupable:
            # shell_run 一类填不出来源身份. 它们的输出不是某个路径在某个状态下的快照,
            # 重跑一次也不保证一样, 所以不参与去重 —— 但仍然可以被降级.
            continue
        anchor = anchors.get(provenance.source_path)
        if anchor is None:
            anchors[provenance.source_path] = slot
            continue
        anchor_provenance = anchor.block.provenance
        assert anchor_provenance is not None
        if anchor_provenance.source_state == provenance.source_state:
            # 决策 3: 同一份内容第二次出现, 后一条换成引用, 锚点不动.
            rewrites[slot.key] = placeholders.dedup_notice(anchor, slot, artifacts)
            continue
        # 文件在两次读之间变了. 旧那条留在原地不动 —— 它陈述的是"我在第 N 次调用时读到
        # 的内容是 X", 这在此刻依然为真, 它是历史; 变的只是它还代不代表当前状态, 而这
        # 一条由紧随其后的新读自己说明.
        anchors[provenance.source_path] = slot
    return rewrites


def changed_notice(slots: tuple[Slot, ...], provenance: ResultProvenance | None) -> str:
    """一条新结果让前面哪几次读作废.

    两种来源, 同一句话:

    - ``mutated_paths`` —— 写入直接说出自己动过谁.
    - ``source_path`` + ``source_state`` —— 同一个路径在不同状态下又读了一次, 那么
      前面那几次读的都不再代表当前内容. 这一条覆盖"文件被工作区之外的东西改了"的情况,
      写入报不出来.

    由循环在回填工具结果时调一次, 结果拼在那条结果的正文末尾. 一次工具结果只回填一次,
    所以天生不会重复 —— 不需要像原地改写那样依赖"改写是幂等的"这个性质.

    没有对应的历史读取就返回空串: 改了一个从没读过的文件, 说"你之前读的不作数了"是废话,
    而废话会稀释真正要紧的那几句.
    """
    if provenance is None:
        return ""
    stale = [slot for slot in slots if _is_stale(slot, provenance)]
    if not stale:
        return ""
    return placeholders.changed_notice(tuple(stale))


def _is_stale(slot: Slot, incoming: ResultProvenance) -> bool:
    earlier = slot.block.provenance
    if earlier is None or not earlier.dedupable:
        return False
    if earlier.source_path in incoming.mutated_paths:
        return True
    # 同路径不同状态: 后一次读回来的才是当前内容.
    return (
        incoming.dedupable
        and earlier.source_path == incoming.source_path
        and earlier.source_state != incoming.source_state
    )
