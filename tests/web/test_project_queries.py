from pathlib import Path

from forgecli.application.project import ProjectService
from forgecli.application.project.project_store import (
    ProjectConfigStore,
    ProjectIndexStore,
)
from forgecli.domain.workspace.project import IndexEntry, ProjectConfig


class MemoryIndex(ProjectIndexStore):
    def __init__(self, entries: dict[str, IndexEntry]) -> None:
        self.entries = entries

    def load(self) -> dict[str, IndexEntry]:
        return dict(self.entries)

    def upsert(self, entry: IndexEntry) -> None:
        self.entries[entry.root] = entry


class MemoryConfigs(ProjectConfigStore):
    def __init__(self, items: dict[str, ProjectConfig]) -> None:
        self.items = items

    def load(self, project_id: str) -> ProjectConfig | None:
        return self.items.get(project_id)

    def save(self, project: ProjectConfig) -> None:
        self.items[project.project_id] = project


def _entry(root: str, project_id: str, *, trusted: bool = True) -> IndexEntry:
    return IndexEntry(
        root=root,
        project_id=project_id,
        trusted=trusted,
        created_at="2026-08-19T10:00:00+08:00",
        updated_at="2026-08-19T10:00:00+08:00",
    )


def test_list_trusted_is_stable_and_ignores_broken_configs(tmp_path: Path) -> None:
    alpha = str((tmp_path / "alpha").resolve())
    zeta = str((tmp_path / "zeta").resolve())
    index = MemoryIndex(
        {
            zeta: _entry(zeta, "zeta"),
            alpha: _entry(alpha, "alpha"),
            "/missing": _entry("/missing", "missing"),
            "/revoked": _entry("/revoked", "revoked", trusted=False),
        }
    )
    configs = MemoryConfigs(
        {
            "zeta": ProjectConfig("zeta", True, zeta),
            "alpha": ProjectConfig("alpha", True, alpha),
            "revoked": ProjectConfig("revoked", True, "/revoked"),
        }
    )

    service = ProjectService(index, configs)

    assert [item.project_id for item in service.list_trusted()] == ["alpha", "zeta"]


def test_get_requires_matching_trusted_index(tmp_path: Path) -> None:
    root = str(tmp_path.resolve())
    project = ProjectConfig("project", True, root)
    configs = MemoryConfigs({"project": project})

    assert ProjectService(MemoryIndex({}), configs).get("project") is None
    assert (
        ProjectService(MemoryIndex({root: _entry(root, "some-other-id")}), configs).get(
            "project"
        )
        is None
    )
    assert (
        ProjectService(MemoryIndex({root: _entry(root, "project")}), configs).get(
            "project"
        )
        == project
    )
