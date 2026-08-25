"""记忆的两条硬边界 (ADR-0033 决策 3 / 10).

这两条是整个 ADR 敢做"静默自动写入"的承重理由. 记忆是模型可写, 且不经人确认的提示词
输入 —— 它能这样, 唯一的前提就是它进不了裁决链: 最坏情况是模型照着一条被污染的记忆
去请求一个动作, 然后照常撞上 ADR-0013 / ADR-0020 / ADR-0027 那整条链.

`scripts/check_arch.py` 里有同样的规则; 这两条测试是给"忘了跑 make arch"兜底
(与 tests/manual_shell/test_trust_boundary.py 同一个理由).
"""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2] / "src" / "forgecli"


def _imports_of(package: Path) -> set[str]:
    found: set[str] = set()
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module)
    return found


def _reaches(package: Path, banned: str) -> set[str]:
    return {
        name
        for name in _imports_of(package)
        if name == banned or name.startswith(f"{banned}.")
    }


def test_memory_never_reaches_the_security_side() -> None:
    """决策 3: 记忆在任何情况下都不参与安全裁决.

    不进 PolicyContext, 不影响 mode 能力矩阵, 不影响学习规则匹配, 不作为审批展示的
    依据. ADR-0009 里那条被划掉的话记着同一件事 —— 这次钉成机器守得住的.
    """
    leaked = _reaches(_ROOT / "application" / "memory", "forgecli.application.security")

    assert leaked == set(), f"记忆不该认识安全模块: {sorted(leaked)}"


def test_the_loop_never_reaches_memory() -> None:
    """决策 10: ADR-0010 §影响明写循环"不能读写长期记忆"."""
    leaked = _reaches(
        _ROOT / "application" / "agent_loop", "forgecli.application.memory"
    )

    assert leaked == set(), f"循环对记忆的依赖数必须是 0: {sorted(leaked)}"


def test_memory_lives_outside_the_workspace() -> None:
    """决策 4: 落工作区会污染用户仓库, 还会被下一轮 Agent 当成项目内容读回上下文.

    附带后果是 fs.* 工具够不到它 —— 这正是想要的隔离.
    """
    from forgecli.infrastructure.config.paths import (
        config_dir,
        project_memory_file,
        user_memory_file,
    )

    for path in (user_memory_file(), project_memory_file("Demo-a1b2c3d4")):
        assert path.is_relative_to(config_dir()), path
