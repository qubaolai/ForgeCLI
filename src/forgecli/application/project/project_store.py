"""项目持久化抽象（由 infrastructure 实现）。

命名沿用既有 ``config_store`` / ``llm_config_store`` 约定：以「存储」表达业务意义，
把「项目索引 / 项目配置存放在哪里、什么格式」隔离在 application 之外。

约定：
    - 两类数据都落在用户级 Forge home 下，不写入被信任的项目目录。
    - load 无文件时返回空（{} 或 None），不抛异常；解析失败抛 ConfigReadError。
    - 写入走 round-trip + 原子替换（由 infrastructure/toml_io 保证）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from forgecli.application.project.project import IndexEntry, ProjectConfig


class ProjectIndexStore(ABC):
    """``projects/index.toml`` 的读写。"""

    @abstractmethod
    def load(self) -> dict[str, IndexEntry]:
        """规范路径 -> IndexEntry；无文件返回 {}。"""

    @abstractmethod
    def upsert(self, entry: IndexEntry) -> None:
        """按 root 路径为 key 新增 / 覆盖一条信任记录。"""


class ProjectConfigStore(ABC):
    """``projects/<project-id>/forge.toml`` 的读写。"""

    @abstractmethod
    def load(self, project_id: str) -> ProjectConfig | None:
        """读取项目配置；不存在返回 None。"""

    @abstractmethod
    def save(self, project: ProjectConfig) -> None:
        """持久化项目配置；首次写入会创建项目目录。"""
