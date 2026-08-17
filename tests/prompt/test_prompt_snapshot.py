"""PromptSnapshot 的构造不变量 (ADR-0018 §3.1).

派生字段与缓存划分都在构造时定死: 一个混在易变尾部里的可缓存块不会报错, 只会让供应商的
自动前缀缓存从命中整段退化成命中开头几十字符, 所以它必须在构造时就造不出来.
"""

from __future__ import annotations

import pytest

from forgecli.domain.agent.prompt import PromptBlock, PromptBlockId, PromptSnapshot


def _block(
    block_id: PromptBlockId, *, cacheable: bool = True, body: str = "正文"
) -> PromptBlock:
    return PromptBlock(
        block_id=block_id, heading=block_id.value, body=body, cacheable=cacheable
    )


def _snapshot(*blocks: PromptBlock) -> PromptSnapshot:
    return PromptSnapshot(version=1, blocks=tuple(blocks))


def test_text_is_derived_from_the_blocks() -> None:
    snapshot = _snapshot(
        _block(PromptBlockId.CORE_IDENTITY, body="身份"),
        _block(PromptBlockId.RUNTIME_FACTS, cacheable=False, body="事实"),
    )

    assert snapshot.text == "# core_identity\n\n身份\n\n# runtime_facts\n\n事实"


def test_the_fingerprint_changes_with_the_text() -> None:
    first = _snapshot(_block(PromptBlockId.CORE_IDENTITY, body="甲"))
    second = _snapshot(_block(PromptBlockId.CORE_IDENTITY, body="乙"))

    assert first.fingerprint != second.fingerprint


def test_the_fingerprint_changes_with_the_version() -> None:
    blocks = (_block(PromptBlockId.CORE_IDENTITY),)

    assert (
        PromptSnapshot(version=1, blocks=blocks).fingerprint
        != PromptSnapshot(version=2, blocks=blocks).fingerprint
    )


def test_a_cacheable_block_after_a_volatile_one_is_rejected() -> None:
    with pytest.raises(ValueError, match="稳定前缀"):
        _snapshot(
            _block(PromptBlockId.RUNTIME_FACTS, cacheable=False),
            _block(PromptBlockId.CORE_IDENTITY, cacheable=True),
        )


def test_an_all_volatile_snapshot_is_allowed() -> None:
    """划分要求的是顺序, 不是"必须有可缓存块"."""
    snapshot = _snapshot(_block(PromptBlockId.RUNTIME_FACTS, cacheable=False))

    assert snapshot.text


def test_an_empty_block_list_is_rejected() -> None:
    with pytest.raises(ValueError, match="blocks"):
        PromptSnapshot(version=1, blocks=())


def test_a_blank_body_is_rejected_at_the_block_level() -> None:
    """条件性块该在 builder 里就不生成, 而不是生成一个空块."""
    with pytest.raises(ValueError, match="body"):
        PromptBlock(
            block_id=PromptBlockId.WORKSPACE_INSTRUCTIONS, heading="项目指令", body="  "
        )


def test_version_must_be_positive() -> None:
    with pytest.raises(ValueError, match="version"):
        PromptSnapshot(version=0, blocks=(_block(PromptBlockId.CORE_IDENTITY),))
