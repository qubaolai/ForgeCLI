"""FORGE.md 的受限读取 (ADR-0018 §5.2, §15.1).

这里全是"读不到该怎么办"的用例. 判据统一: 提示词是辅助机制, 一份读不了的 FORGE.md
不该让整个会话起不来, 所以一律跳过而不是抛错.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from forgecli.application.prompt.project_instruction_reader import (
    MAX_INSTRUCTION_BYTES,
    TRUNCATION_NOTICE,
)
from forgecli.infrastructure.prompt import FsProjectInstructionReader


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    return root


def _read(*roots: Path) -> tuple[object, ...]:
    return FsProjectInstructionReader().read(tuple(str(root) for root in roots))


def test_a_missing_forge_md_is_not_an_error(workspace: Path) -> None:
    assert _read(workspace) == ()


def test_a_top_level_forge_md_is_read(workspace: Path) -> None:
    (workspace / "FORGE.md").write_text("注释用中文\n", encoding="utf-8")

    found = _read(workspace)

    assert len(found) == 1
    assert "注释用中文" in found[0].text  # type: ignore[attr-defined]
    assert found[0].source_id == "primary:FORGE.md"  # type: ignore[attr-defined]


def test_nested_forge_md_is_not_discovered(workspace: Path) -> None:
    """只查每个受信任根的顶层. 嵌套指令的作用域与优先级需要单独 ADR."""
    nested = workspace / "src"
    nested.mkdir()
    (nested / "FORGE.md").write_text("不该被读到", encoding="utf-8")

    assert _read(workspace) == ()


def test_additional_roots_are_read_in_order(workspace: Path, tmp_path: Path) -> None:
    extra = tmp_path / "extra"
    extra.mkdir()
    (workspace / "FORGE.md").write_text("主根", encoding="utf-8")
    (extra / "FORGE.md").write_text("附加根", encoding="utf-8")

    found = _read(workspace, extra)

    assert [item.source_id for item in found] == [  # type: ignore[attr-defined]
        "primary:FORGE.md",
        "added:FORGE.md",
    ]


def test_a_symlink_escaping_the_root_is_refused(
    workspace: Path, tmp_path: Path
) -> None:
    """工作区里一个指向区外的 FORGE.md 软链接, 字面路径看着在区内."""
    outside = tmp_path / "outside.md"
    outside.write_text("区外内容", encoding="utf-8")
    os.symlink(outside, workspace / "FORGE.md")

    assert _read(workspace) == ()


def test_a_file_with_nul_bytes_is_skipped(workspace: Path) -> None:
    """含 NUL 说明它不是文本. 不猜, 直接跳过."""
    (workspace / "FORGE.md").write_bytes(b"before\x00after")

    assert _read(workspace) == ()


def test_non_utf8_content_is_skipped(workspace: Path) -> None:
    (workspace / "FORGE.md").write_bytes(b"\xff\xfe\xfd")

    assert _read(workspace) == ()


def test_an_empty_file_is_skipped(workspace: Path) -> None:
    (workspace / "FORGE.md").write_text("   \n\n", encoding="utf-8")

    assert _read(workspace) == ()


def test_an_oversized_file_is_truncated_with_an_explicit_notice(
    workspace: Path,
) -> None:
    """超限时显式加标记, 不静默截断 —— 模型据此知道自己看到的不是全部."""
    (workspace / "FORGE.md").write_text("x" * (MAX_INSTRUCTION_BYTES + 500), "utf-8")

    found = _read(workspace)

    assert len(found) == 1
    assert found[0].text.endswith(TRUNCATION_NOTICE)  # type: ignore[attr-defined]


def test_truncation_cuts_on_a_character_boundary(workspace: Path) -> None:
    """切在多字节字符中间时回退到最后一个完整字符, 不产生半个字."""
    # 中文每字 3 字节, 上限不是 3 的倍数, 因此截断点必然落在字符中间.
    (workspace / "FORGE.md").write_text("中" * (MAX_INSTRUCTION_BYTES // 2), "utf-8")

    found = _read(workspace)

    assert len(found) == 1
    assert found[0].text.endswith(TRUNCATION_NOTICE)  # type: ignore[attr-defined]
    # 能取到就说明没有半个字符 —— 有的话上面 read 时就抛了.
    assert "中" in found[0].text  # type: ignore[attr-defined]


def test_a_directory_named_forge_md_is_skipped(workspace: Path) -> None:
    (workspace / "FORGE.md").mkdir()

    assert _read(workspace) == ()


def test_the_total_budget_stops_later_roots(workspace: Path, tmp_path: Path) -> None:
    """单文件上限之外还有总量上限: 三个根各塞一个大文件不该无限累加."""
    roots = [workspace]
    for index in range(3):
        extra = tmp_path / f"extra{index}"
        extra.mkdir()
        roots.append(extra)
    for root in roots:
        (root / "FORGE.md").write_text("y" * MAX_INSTRUCTION_BYTES, encoding="utf-8")

    found = _read(*roots)

    total = sum(len(item.text.encode("utf-8")) for item in found)  # type: ignore[attr-defined]
    assert total <= 64 * 1024 + len(TRUNCATION_NOTICE.encode("utf-8")) + 1
    assert len(found) < len(roots)
