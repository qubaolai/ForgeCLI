"""ProjectService + TOML 存储的验收测试（ADR-0008 / 2026-06-26）。

覆盖 roadmap 验收点：首次信任建 forge.toml + 索引、不在仓库建 .forge、
拒绝不建文件、已信任再启动/子目录启动复用、路径边界匹配、最长匹配、
工作区目录归一 / 去重 / 非法报错、索引引号 key round-trip。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from forgecli.application.project import (
    ProjectConfig,
    ProjectService,
    WorkspaceError,
)
from forgecli.infrastructure.project import (
    TomlProjectConfigStore,
    TomlProjectIndexStore,
)

_CLOCK = "2026-06-26T10:00:00+08:00"


def _service(home: Path) -> ProjectService:
    projects = home / "projects"
    return ProjectService(
        TomlProjectIndexStore(projects / "index.toml"),
        TomlProjectConfigStore(projects),
        clock=lambda: _CLOCK,
    )


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    return repo


# ---- 首次信任 ----


def test_untrusted_directory_is_not_found(tmp_path: Path) -> None:
    service = _service(tmp_path / "home")

    assert service.find_trusted(_repo(tmp_path)) is None


def test_trust_creates_project_and_index_without_polluting_repo(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    repo = _repo(tmp_path)
    service = _service(home)

    project = service.trust(repo)

    project_file = home / "projects" / project.project_id / "forge.toml"
    assert project_file.exists()
    assert (home / "projects" / "index.toml").exists()
    # 信任绝不在被信任目录下创建 .forge。
    assert not (repo / ".forge").exists()
    assert project.workspace_roots == (str(repo.resolve()),)
    assert project.primary_workspace_root == str(repo.resolve())
    assert project.trusted is True


def test_trusted_root_and_subdir_rebind_without_prompt(tmp_path: Path) -> None:
    service = _service(tmp_path / "home")
    repo = _repo(tmp_path)
    created = service.trust(repo)

    from_root = service.find_trusted(repo)
    from_subdir = service.find_trusted(repo / "src")

    assert from_root is not None and from_subdir is not None
    assert from_root.project_id == from_subdir.project_id == created.project_id


def test_path_boundary_prefix_sibling_does_not_match(tmp_path: Path) -> None:
    service = _service(tmp_path / "home")
    repo = _repo(tmp_path)
    service.trust(repo)
    sibling = tmp_path / "repo-extra"  # 与 "repo" 共享字符串前缀
    sibling.mkdir()

    assert service.find_trusted(sibling) is None


def test_longest_root_wins_on_nested_trust(tmp_path: Path) -> None:
    service = _service(tmp_path / "home")
    outer = _repo(tmp_path)
    inner = outer / "src"
    service.trust(outer)
    inner_project = service.trust(inner)

    hit = service.find_trusted(inner)

    assert hit is not None
    assert hit.project_id == inner_project.project_id


# ---- 工作区目录 ----


def test_add_workspace_dir_normalizes_relative_and_dedups(tmp_path: Path) -> None:
    service = _service(tmp_path / "home")
    repo = _repo(tmp_path)
    project = service.trust(repo)

    resolved = service.normalize_workspace_dir("src", repo)
    added = service.add_workspace_dir(project, resolved)

    assert str(resolved) in added.workspace_roots
    assert len(added.workspace_roots) == 2
    # 重复添加无感跳过。
    again = service.add_workspace_dir(added, resolved)
    assert len(again.workspace_roots) == 2


def test_add_workspace_dir_persists_across_instances(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = _repo(tmp_path)
    service = _service(home)
    project = service.trust(repo)
    service.add_workspace_dir(project, service.normalize_workspace_dir("src", repo))

    reloaded = _service(home).find_trusted(repo)

    assert reloaded is not None
    assert str((repo / "src").resolve()) in reloaded.workspace_roots


def test_normalize_rejects_missing_and_non_directory(tmp_path: Path) -> None:
    service = _service(tmp_path / "home")
    repo = _repo(tmp_path)
    (repo / "file.txt").write_text("x", encoding="utf-8")

    with pytest.raises(WorkspaceError):
        service.normalize_workspace_dir("does/not/exist", repo)
    with pytest.raises(WorkspaceError):
        service.normalize_workspace_dir("file.txt", repo)
    with pytest.raises(WorkspaceError):
        service.normalize_workspace_dir("   ", repo)


# ---- 存储 round-trip ----


def test_index_quoted_path_key_round_trips(tmp_path: Path) -> None:
    projects = tmp_path / "home" / "projects"
    store = TomlProjectIndexStore(projects / "index.toml")
    service = _service(tmp_path / "home")
    repo = _repo(tmp_path)
    service.trust(repo)

    entries = store.load()

    key = str(repo.resolve())
    assert key in entries
    assert entries[key].project_id.startswith("repo-")


def test_project_config_store_missing_returns_none(tmp_path: Path) -> None:
    store = TomlProjectConfigStore(tmp_path / "projects")

    assert store.load("nope-deadbeef") is None


def test_project_config_round_trips_workspace_roots(tmp_path: Path) -> None:
    store = TomlProjectConfigStore(tmp_path / "projects")
    project = ProjectConfig(
        project_id="repo-deadbeef",
        trusted=True,
        primary_workspace_root="/a",
        workspace_roots=("/a", "/b"),
    )

    store.save(project)

    assert store.load("repo-deadbeef") == project


def test_project_config_store_preserves_config_section(tmp_path: Path) -> None:
    # ProjectConfigStore 只写状态字段；ConfigService 写的 [logging] 等偏好段不被覆盖。
    store = TomlProjectConfigStore(tmp_path / "projects")
    project = ProjectConfig(
        project_id="repo-deadbeef",
        trusted=True,
        primary_workspace_root="/a",
        workspace_roots=("/a",),
    )
    store.save(project)
    forge = tmp_path / "projects" / "repo-deadbeef" / "forge.toml"
    forge.write_text(
        forge.read_text(encoding="utf-8") + '\n[logging]\nlevel = "debug"\n',
        encoding="utf-8",
    )

    # 再次保存状态（如 /add-dir）不应抹掉 [logging]。
    store.save(replace(project, workspace_roots=("/a", "/b")))

    text = forge.read_text(encoding="utf-8")
    assert 'level = "debug"' in text
    assert "/b" in text
