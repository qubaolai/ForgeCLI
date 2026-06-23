"""project-id 生成：目录名前缀 + 规范路径 hash，稳定且按路径区分。"""

from __future__ import annotations

from pathlib import Path

from forgecli.application.project import generate_project_id


def test_id_has_name_prefix_and_hash_suffix() -> None:
    pid = generate_project_id(Path("/Users/x/ForgeCLI"))

    assert pid.startswith("ForgeCLI-")
    suffix = pid.rsplit("-", 1)[1]
    assert len(suffix) == 8
    assert all(c in "0123456789abcdef" for c in suffix)


def test_same_path_yields_same_id() -> None:
    root = Path("/Users/x/ForgeCLI")

    assert generate_project_id(root) == generate_project_id(root)


def test_different_paths_yield_different_ids() -> None:
    # 同名目录但不同位置：hash 不同，id 不冲突。
    a = generate_project_id(Path("/Users/x/ForgeCLI"))
    b = generate_project_id(Path("/Users/y/ForgeCLI"))

    assert a != b
    assert a.startswith("ForgeCLI-") and b.startswith("ForgeCLI-")


def test_unsafe_name_chars_are_sanitized() -> None:
    pid = generate_project_id(Path("/tmp/my project (v2)"))

    name = pid.rsplit("-", 1)[0]
    assert all(c.isalnum() or c in "._-" for c in name)
