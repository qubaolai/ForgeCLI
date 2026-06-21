"""TOML 文件读写的统一入口（全部经 tomlkit）。

项目里所有 .toml 的解析 / 序列化 / 原子写都收敛到这里，保证：
    - 单一依赖：只用 tomlkit，不混用 tomllib + 手写 writer。
    - round-trip：解析后再写出保留注释与既有结构（人机共编友好）。
    - 一致的错误：解析 / IO 失败统一翻成面向用户的 ConfigReadError，不漏 traceback。
    - 一致的落盘：先写临时文件再 rename，避免读到半截文件。
"""

from __future__ import annotations

from pathlib import Path

import tomlkit
from tomlkit import TOMLDocument
from tomlkit.exceptions import TOMLKitError

from forgecli.shared.errors import ConfigReadError


def read_document(path: Path) -> TOMLDocument:
    """解析 TOML 文档；文件不存在返回空文档；解析 / IO 失败抛 ConfigReadError。"""
    if not path.exists():
        return tomlkit.document()
    try:
        return tomlkit.parse(path.read_text(encoding="utf-8"))
    except TOMLKitError as exc:
        raise ConfigReadError(
            f"配置文件存在语法错误，无法读取：{path}\n  原因：{exc}"
        ) from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigReadError(f"无法读取配置文件：{path}\n  原因：{exc}") from exc


def write_document(path: Path, doc: TOMLDocument) -> None:
    """原子写出 TOML 文档（先临时文件再替换）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(tomlkit.dumps(doc), encoding="utf-8")
    tmp.replace(path)
