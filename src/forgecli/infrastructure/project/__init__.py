"""项目存储的 infrastructure 适配器。"""

from forgecli.infrastructure.project.toml_store import (
    TomlProjectConfigStore,
    TomlProjectIndexStore,
)

__all__ = ["TomlProjectConfigStore", "TomlProjectIndexStore"]
