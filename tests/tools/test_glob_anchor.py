"""glob 展开的遍历起点 (2026-09-04 修复).

守的是一次实测事故: 分析器把 `sed` 脚本里的 `/api/orders/**` 当成目标候选去 glob,
而 `expand_glob` 见绝对模式就锚在 `/`, 于是一次裁决 `os.walk` 了整块磁盘 —— 耗时
183 秒 (forge-20260904-133236 的 step 51), 同一会话另一条 `python3 -c` 耗时 99 秒.

这类退化不会报错, 只会表现为"卡住了", 而卡住的原因在日志里看不出来. 所以这一组同时
钉住两件事: 起点确实下沉了, **而且**下沉没有把忽略规则一起带走.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from forgecli.infrastructure.workspace.os_filesystem_view import (
    OsFileSystemView,
    _literal_prefix,
)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "src" / "deep" / "nested").mkdir(parents=True)
    (tmp_path / "src" / "a.py").write_text("a", encoding="utf-8")
    (tmp_path / "src" / "deep" / "b.py").write_text("b", encoding="utf-8")
    (tmp_path / "src" / "deep" / "nested" / "c.py").write_text("c", encoding="utf-8")
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "d.py").write_text("d", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("api/orders/**", ("api/orders", "**")),
        ("src/forgecli/**/*.py", ("src/forgecli", "**/*.py")),
        # 最后一段永远留给 pattern: 否则起点会变成那个文件本身.
        ("docs/readme.md", ("docs", "readme.md")),
        # 首段就带通配符: 无从下沉, 原样交出去.
        ("**/*.py", ("", "**/*.py")),
        ("*.java", ("", "*.java")),
        # `..` 会把起点带出调用方给的 root, 不并入.
        ("../outside/**", ("", "../outside/**")),
    ],
)
def test_the_literal_prefix_is_absorbed_into_the_walk_root(
    pattern: str, expected: tuple[str, str]
) -> None:
    assert _literal_prefix(pattern) == expected


def test_an_absolute_pattern_does_not_walk_the_whole_disk() -> None:
    """`/api/orders/**` 这种不存在的绝对模式必须立刻收工, 而不是从 `/` 开始遍历.

    用挂钟断言是刻意的: 这里要防的就是耗时, 而耗时正是当初唯一的症状. 阈值放到 2 秒
    (实测 0.3 毫秒), 只有真的退回全盘遍历才会撞上.
    """
    started = time.perf_counter()
    matched = OsFileSystemView().expand_glob("/api/orders/**", root="/tmp")
    elapsed = time.perf_counter() - started
    assert matched == ()
    assert elapsed < 2.0, f"绝对模式仍在从根遍历: 耗时 {elapsed:.1f}s"


def test_a_deep_pattern_still_finds_everything_under_it(tree: Path) -> None:
    """起点下沉不能改变结果集."""
    matched = OsFileSystemView().expand_glob("src/**/*.py", root=str(tree))
    assert [Path(item).name for item in matched] == ["a.py", "b.py", "c.py"]


def test_the_absorbed_prefix_still_goes_through_the_ignore_rules(tree: Path) -> None:
    """被并进起点的那一段仍然要参与忽略判断.

    这是下沉最容易带走的东西: 起点变成 `<root>/node_modules` 之后, 遍历里的相对路径
    再也不含 `node_modules` 这一段, 忽略规则于是判不出来. 判断必须仍按调用方给的 root
    提问 —— 起点是性能, root 是语义.
    """
    view = OsFileSystemView()
    assert view.expand_glob("node_modules/**/*.py", root=str(tree)) == ()
    kept = view.expand_glob("node_modules/**/*.py", root=str(tree), skip_ignored=False)
    assert [Path(item).name for item in kept] == ["d.py"]


def test_the_result_limit_still_applies_after_anchoring(tree: Path) -> None:
    matched = OsFileSystemView().expand_glob(
        "src/**/*.py", root=str(tree), max_results=2
    )
    assert len(matched) == 2
