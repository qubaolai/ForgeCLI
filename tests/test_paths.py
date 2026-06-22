"""配置文件位置解析：环境变量覆盖、默认用户主目录、与 cwd 解耦。"""

from __future__ import annotations

from pathlib import Path

import pytest

from forgecli.infrastructure.config import (
    CONFIG_DIR_ENV,
    config_dir,
    config_file,
    paths,
)


def test_defaults_to_home_forge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(CONFIG_DIR_ENV, raising=False)
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path))

    assert config_dir() == tmp_path / ".forge"


def test_env_override_takes_precedence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "custom"
    monkeypatch.setenv(CONFIG_DIR_ENV, str(target))

    assert config_dir() == target


def test_env_override_expands_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CONFIG_DIR_ENV, "~/somewhere/forge")

    resolved = config_dir()
    assert resolved.is_absolute()
    assert "~" not in str(resolved)


def test_blank_env_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(CONFIG_DIR_ENV, "   ")
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path))

    assert config_dir() == tmp_path / ".forge"


def test_config_dir_is_independent_of_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # 配置目录不应随当前工作目录变化（与 workspace 解耦）。
    home = tmp_path / "home"
    workdir = tmp_path / "workdir"
    home.mkdir()
    workdir.mkdir()
    monkeypatch.delenv(CONFIG_DIR_ENV, raising=False)
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(workdir)

    assert config_dir() == home / ".forge"


def test_config_file_joins_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))

    assert config_file("llm.toml") == tmp_path / "llm.toml"
    assert config_file("config.toml") == tmp_path / "config.toml"
