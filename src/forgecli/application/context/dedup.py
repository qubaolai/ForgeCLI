"""回合内内容去重与文件变更通知 (ADR-0032 决策 3 / 4).

两件事同一份判据, 所以同一个 pass: 走一遍 transcript, 把每条工具结果按它自己记下的
``(source_path, source_state)`` 与前面同路径的那一条比一比.

- 状态相同 -> 后一条是重复的, 换成一行引用. **省的是 token 不是 IO**: 文件照读,
  stat 照做, 省掉的是把同一份 16 KiB 再塞一遍进上下文.
- 状态不同 -> 前一条已经过时, 降级并写明"该文件此后被修改过". 这是**主动**通知,
  模型不需要想起来去问.

## 为什么这个 pass 是无状态的

它不存表, 每次从 transcript 重新算. 于是"文件变没变"这个判断的两个输入, 都是各自那次
工具调用**在自己执行的那一刻**记下的 token —— 中间不管发生过什么, 后一次读必然拿到
当时的真实状态.

这一点让 ADR-0032 决策 5 (去重表注册进 ManualMutationBarrier) 变得没有必要: 屏障要
失效的是"跨人工 Shell 复用的旧事实", 而这里根本没有旧事实可复用. 一个存起来的表才需要
那道闸; 注册一个无表可清的钩子, 正是 ADR-0028 规则 C 说的不生效的安全阀.

## 判据复用 path_state_token

``source_state`` 由工具调用 ``path_state_token`` 算出, 与 ADR-0027 执行前复核用的是
同一个函数. 两套判据会让安全层说"没变"而这里说"变了", 而这种分歧不会报错.
"""

from __future__ import annotations

from forgecli.application.context import placeholders
from forgecli.application.context.transcript import Slot
from forgecli.application.tools.artifact_store import ArtifactStore

__all__ = ["plan_rewrites"]


def plan_rewrites(
    slots: tuple[Slot, ...], artifacts: ArtifactStore | None
) -> dict[tuple[int, int], str]:
    """算出这一轮 transcript 上要改写哪些块, 改成什么."""
    rewrites: dict[tuple[int, int], str] = {}
    # 每个路径当前的"代表": 后面同路径的结果都与它比.
    anchors: dict[str, Slot] = {}
    for slot in slots:
        provenance = slot.block.provenance
        if provenance is None or not provenance.dedupable:
            # shell.run 一类填不出来源身份. 它们的输出不是某个路径在某个状态下的快照,
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
        # 决策 4: 文件变了. 前一条降级并写明它已经不代表当前内容, 这一条成为新锚点.
        #
        # **不是**去改这一条: 那条旧记录陈述的是"我在第 N 次调用时读到的内容是 X",
        # 这在此刻依然为真, 它是历史. 变的是它还代不代表当前状态.
        rewrites[anchor.key] = placeholders.stale_notice(anchor, artifacts)
        anchors[provenance.source_path] = slot
    return rewrites
