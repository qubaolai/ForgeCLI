"""一级降级: 把工具结果正文换成 artifact 引用 (ADR-0032 决策 2).

确定性, 可单测, 不花钱, 3 天内可逆. **能用一级解决的绝不用二级** —— 后者引入不可
复现性, 一段被摘要吃掉的原文再也拼不回来.

两条取舍写在这里:

- **从最旧的开始降.** 模型刚请求回来的那几条正是它当前在用的; 先降它们, 等于花了
  压缩的代价却马上要它再取一次.
- **留最近几条不动.** 同上, 而且留一手也让"压完还是放不下"这个结论更可信 —— 如果
  连最近两条都放不下, 那确实该走二级了.
"""

from __future__ import annotations

from forgecli.application.context import placeholders
from forgecli.application.context.transcript import Slot
from forgecli.application.tools.artifact_store import ArtifactStore

__all__ = ["KEEP_RECENT_RESULTS", "plan_rewrites"]

# 末尾几条工具结果不降级.
#
# 2 而不是 1: 模型常常是"读一个文件, 再 grep 一下, 然后动手改", 动手那一步同时要用到
# 前两条. 只留一条会让它在改之前先把刚丢掉的那条取回来, 净效果是多一次工具调用.
KEEP_RECENT_RESULTS = 2


def plan_rewrites(
    slots: tuple[Slot, ...],
    artifacts: ArtifactStore | None,
    *,
    already: dict[tuple[int, int], str],
) -> dict[tuple[int, int], str]:
    """算出要降级哪些块.

    ``already`` 是去重那一遍已经排好的改写. 同一个位置不重复安排 —— 去重给出的引用
    比降级引用信息更多 (它还说了"与第几次相同"), 覆盖掉是净损失.
    """
    rewrites: dict[tuple[int, int], str] = {}
    candidates = slots[: max(0, len(slots) - KEEP_RECENT_RESULTS)]
    for slot in candidates:
        if slot.key in already:
            continue
        provenance = slot.block.provenance
        if provenance is None or not provenance.archived:
            # 没归档就没得降: 换成引用等于把内容删了还不告诉人去哪找.
            continue
        notice = placeholders.archived_notice(slot, artifacts)
        if len(notice) >= len(slot.block.content):
            # 换上去反而更长, 或者这一条已经是占位文本了. 后者让整个 pass 幂等 ——
            # 同一份 transcript 反复 fit 不会一次次记成"又降了一批".
            continue
        rewrites[slot.key] = notice
    return rewrites
