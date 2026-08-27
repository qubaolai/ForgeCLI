"""受控 PATH 里有什么 (ADR-0014 §4.2).

探测出来的候选目录只有那几个系统位置. 装在别处的 maven / jdk / node 因此在受控 PATH 上
根本不存在, 而 `execution.toolchain_dirs` 是唯一一条把它们加进来的路 —— 这条路断了的
后果不是"少一个便利", 是模型跑不了构建, 于是永远验证不了自己写的代码能不能起来.
"""

from __future__ import annotations

import os
from pathlib import Path

from forgecli.domain.config import config_keys
from forgecli.domain.config.effective_config import EffectiveConfig
from forgecli.infrastructure.execution.environment_probe import (
    build_execution_environment,
    probe_execution_profile,
)


def _effective(raw: str) -> EffectiveConfig:
    return EffectiveConfig.from_overrides({config_keys.EXECUTION_TOOLCHAIN_DIRS: raw})


def test_unset_toolchain_dirs_reads_as_empty() -> None:
    assert EffectiveConfig.from_overrides({}).toolchain_dirs == ()


def test_toolchain_dirs_split_on_the_platform_separator() -> None:
    raw = os.pathsep.join(("/opt/maven/bin", "/opt/jdk/bin"))

    assert _effective(raw).toolchain_dirs == ("/opt/maven/bin", "/opt/jdk/bin")


def test_blank_entries_do_not_become_path_entries() -> None:
    """空条目进 PATH 会被 shell 当成当前目录, 而 `.` 正是这份 PATH 要挡的东西."""
    raw = os.pathsep.join(("/opt/maven/bin", "", "  "))

    assert _effective(raw).toolchain_dirs == ("/opt/maven/bin",)


def test_configured_toolchain_reaches_the_controlled_path(tmp_path: Path) -> None:
    toolchain = tmp_path / "apache-maven" / "bin"
    toolchain.mkdir(parents=True)
    configured = _effective(str(toolchain)).toolchain_dirs

    profile = probe_execution_profile(
        protected_roots_hash="protected", toolchain_dirs=configured
    )

    assert str(toolchain) in profile.trusted_path
    assert str(toolchain) in profile.writable_toolchain_path
    assert str(toolchain) in build_execution_environment(profile, raw={})["PATH"]
