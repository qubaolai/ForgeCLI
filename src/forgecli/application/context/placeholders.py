"""顶替工具结果正文的那一行字 (ADR-0032 决策 2 / 3 / 4 / 6.1).

三条通路都要生成占位文本, 而它们必须**分得开可取回与取不回来**. 混成一句"内容见
xxx"的后果是模型去取一个已经被回收的 id, 拿回一条找不到, 而它读不出这是清理机制还是
自己 id 写错了 —— 后一种理解会让它反复重试, 直到撞满 _MAX_BLOCKED_CALLS.

措辞本身在 domain/prompt/text.py, 受 PROMPT_TEXT_VERSION 管辖 (ADR-0031). 这里只决定
**哪一句用在什么情况**.
"""

from __future__ import annotations

from forgecli.application.context.transcript import Slot
from forgecli.application.tools.artifact_store import ArtifactStore
from forgecli.domain.prompt import text as prompt_text

__all__ = ["archived_notice", "dedup_notice", "stale_notice"]


def _retrievable(slot: Slot, artifacts: ArtifactStore | None) -> str:
    """这条结果的正文还取不取得回来; 取得回来就返回它的 id, 否则返回空串.

    真的去问存储, 不是看 provenance 上有没有 id: 有 id 只说明当初存过, 而 3 天的过期
    回收之后它可能已经不在了 (决策 6.1).
    """
    provenance = slot.block.provenance
    if provenance is None or not provenance.archived:
        return ""
    if artifacts is None or not artifacts.exists(provenance.artifact_id):
        return ""
    # 还要用, 就给它续期 —— mtime 是唯一的引用时钟 (决策 6).
    artifacts.touch(provenance.artifact_id)
    return provenance.artifact_id


def archived_notice(slot: Slot, artifacts: ArtifactStore | None) -> str:
    """一级降级: 正文换成引用."""
    artifact_id = _retrievable(slot, artifacts)
    if not artifact_id:
        return prompt_text.ARCHIVED_EXPIRED.format(index=slot.ordinal)
    provenance = slot.block.provenance
    size = 0 if provenance is None else provenance.byte_size
    return prompt_text.ARCHIVED_AVAILABLE.format(
        index=slot.ordinal, artifact_id=artifact_id, size=size
    )


def stale_notice(slot: Slot, artifacts: ArtifactStore | None) -> str:
    """决策 4: 这一条读到的内容, 之后文件被改过了."""
    artifact_id = _retrievable(slot, artifacts)
    if not artifact_id:
        return prompt_text.ARCHIVED_EXPIRED.format(index=slot.ordinal)
    return prompt_text.ARCHIVED_STALE.format(
        index=slot.ordinal, artifact_id=artifact_id
    )


def dedup_notice(anchor: Slot, duplicate: Slot, artifacts: ArtifactStore | None) -> str:
    """决策 3: 与前面某一次读到的完全相同.

    id 取**重复那一条自己的** —— 同路径同状态读出的是同一段字节, 内容寻址因此给出同一
    个 id. 取锚点的也一样, 但取自己的不必假设两者相等.
    """
    artifact_id = _retrievable(duplicate, artifacts)
    if not artifact_id:
        return prompt_text.ARCHIVED_EXPIRED.format(index=duplicate.ordinal)
    return prompt_text.DEDUP_UNCHANGED.format(
        index=anchor.ordinal, artifact_id=artifact_id
    )
