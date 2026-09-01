"""顶替工具结果正文的那一行字 (ADR-0032 决策 2 / 3 / 4 / 6.1).

三条通路都要生成占位文本, 而它们必须**分得开可取回与取不回来**. 混成一句"内容见
xxx"的后果是模型去取一个已经被回收的 id, 拿回一条找不到, 而它读不出这是清理机制还是
自己 id 写错了 —— 后一种理解会让它反复重试, 直到撞满 _MAX_BLOCKED_CALLS.

措辞本身在 domain/prompt/text.py, 受 PROMPT_TEXT_VERSION 管辖 (ADR-0031). 这里只决定
**哪一句用在什么情况**.
"""

from __future__ import annotations

from forgecli.application.context.transcript import Slot
from forgecli.application.prompt.template_renderer import render_notice
from forgecli.application.tools.artifact_store import ArtifactStore

__all__ = ["archived_notice", "changed_notice", "dedup_notice"]


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
        return render_notice("context.archived_expired", index=slot.ordinal)
    provenance = slot.block.provenance
    size = 0 if provenance is None else provenance.byte_size
    return render_notice(
        "context.archived_available",
        index=slot.ordinal,
        artifact_id=artifact_id,
        size=size,
    )


def changed_notice(stale: tuple[Slot, ...]) -> str:
    """写入之后追加的一句话: 前面这几次读到的内容不再代表当前状态.

    不带 artifact 引用: 那份旧内容还原样躺在 transcript 里, 模型往回翻就看得到, 让它
    去取一次只是白花一次工具调用. 这与降级占位不同 —— 那里正文已经被换掉了, 不给引用
    就真的找不回来.

    列序号而不是路径: 序号能对上 transcript 里那一条, 而同一个路径可能被读过好几次,
    只说路径分不清指的是哪一次.
    """
    return render_notice(
        "context.changed_after_write",
        entries=tuple((slot.ordinal, _path_of(slot)) for slot in stale),
    )


def _path_of(slot: Slot) -> str:
    provenance = slot.block.provenance
    return "" if provenance is None else provenance.source_path


def dedup_notice(anchor: Slot, duplicate: Slot, artifacts: ArtifactStore | None) -> str:
    """决策 3: 与前面某一次读到的完全相同.

    id 取**重复那一条自己的** —— 同路径同状态读出的是同一段字节, 内容寻址因此给出同一
    个 id. 取锚点的也一样, 但取自己的不必假设两者相等.
    """
    artifact_id = _retrievable(duplicate, artifacts)
    if not artifact_id:
        return render_notice("context.archived_expired", index=duplicate.ordinal)
    return render_notice(
        "context.dedup_unchanged", index=anchor.ordinal, artifact_id=artifact_id
    )
