"""受控 PATH 里有什么.

判据是 `Agent 环境 = 开发者环境 - Agent 可写的部分`. 落到 PATH 上就是: 继承启动 shell
的 PATH, 只减去相对路径条目与落在工作区里的目录.

早先这里是一份写死的候选目录清单加一个 `execution.toolchain_dirs` 配置补丁. 那个方向
按定义补不完 —— 装在 `~/Documents/...` 下的 maven 与 `~/Library/Java/...` 下的 jdk 都
不在清单里, 于是终端里跑得起来的构建在 Forge 里报"命令不存在", 而模型读不出这是没装
还是被拿掉了.
"""

from __future__ import annotations

from pathlib import Path

from forgecli.domain.config import config_keys
from forgecli.domain.config.effective_config import EffectiveConfig
from forgecli.domain.execution.environment import EnvironmentInheritance
from forgecli.infrastructure.execution.environment_probe import (
    build_execution_environment,
    probe_execution_profile,
)


def _effective(raw: str) -> EffectiveConfig:
    return EffectiveConfig.from_overrides({config_keys.EXECUTION_ENV_INHERIT: raw})


def test_inheritance_defaults_to_all() -> None:
    """默认必须是全继承: 等价性是主要需求, 收紧是可选项."""
    assert (
        EffectiveConfig.from_overrides({}).environment_inheritance
        is EnvironmentInheritance.ALL
    )


def test_each_tier_is_readable_from_config() -> None:
    assert _effective("core").environment_inheritance is EnvironmentInheritance.CORE
    assert _effective("none").environment_inheritance is EnvironmentInheritance.NONE


def test_an_unreadable_tier_falls_back_instead_of_failing_the_session() -> None:
    """手滑写错一个取值不该让整个会话起不来; as_dict 会把真实取值显示出来."""
    assert _effective("nonsense").environment_inheritance is EnvironmentInheritance.ALL


def test_a_tool_outside_the_system_directories_reaches_the_controlled_path(
    tmp_path: Path,
) -> None:
    """这条是整个改动的理由: 装在别处的 maven / jdk 必须能用, 且不需要任何配置."""
    toolchain = tmp_path / "apache-maven" / "bin"
    toolchain.mkdir(parents=True)

    profile = probe_execution_profile(
        protected_roots_hash="protected",
        inherited_path=f"{toolchain}:/usr/bin",
    )

    assert str(toolchain) in profile.trusted_path
    assert str(toolchain) in build_execution_environment(profile, raw={})["PATH"]


def test_workspace_directories_are_subtracted(tmp_path: Path) -> None:
    """`node_modules/.bin` 与 `.venv/bin` 都在工作区里, 而那是 Agent 自己能写的地方."""
    workspace = tmp_path / "ws"
    (workspace / "node_modules" / ".bin").mkdir(parents=True)

    profile = probe_execution_profile(
        protected_roots_hash="protected",
        workspace_roots=(str(workspace),),
        inherited_path=f"{workspace / 'node_modules' / '.bin'}:/usr/bin",
    )

    assert profile.trusted_path == ("/usr/bin",)


def test_the_inherited_order_is_preserved() -> None:
    """开发者把 temurin-11 排在 /usr/bin 前面是有意的; 重排等于换了一个 java."""
    profile = probe_execution_profile(
        protected_roots_hash="protected",
        inherited_path="/opt/jdk11/bin:/usr/bin:/opt/maven/bin",
    )

    assert profile.trusted_path == ("/opt/jdk11/bin", "/usr/bin", "/opt/maven/bin")
