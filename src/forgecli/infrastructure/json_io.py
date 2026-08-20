"""JSON 文档读写的统一入口。

项目里所有持久化文档（应用配置、LLM 配置、项目索引与配置、学习规则、计划与待办）的
解析 / 序列化 / 原子写都收敛到这里，保证：

    - 单一实现：不在每个 store 里各写一遍 ``json.loads`` 与临时文件替换。
    - 一致的错误：解析 / IO 失败统一翻成面向用户的 ``ConfigReadError``，不漏 traceback。
    - 一致的落盘：先写临时文件再 rename，避免读到半截文件。

会话状态另有 ``infrastructure/session/json_io``：它把失败翻成 ``SessionStateError``。
两处的错误语义不同，合成一个模块只会让调用方分不清该 catch 什么；原子写这一半是共用的。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from forgecli.shared.errors import ConfigReadError

__all__ = ["read_document", "write_document"]


def read_document(path: Path) -> dict[str, Any]:
    """解析 JSON 对象；文件不存在返回空 dict；解析 / IO 失败抛 ConfigReadError。"""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigReadError(
            f"配置文件存在语法错误，无法读取：{path}\n  原因：{exc}"
        ) from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigReadError(f"无法读取配置文件：{path}\n  原因：{exc}") from exc
    if not isinstance(data, dict):
        raise ConfigReadError(f"配置文件格式不正确（应为 JSON 对象）：{path}")
    return {str(key): value for key, value in data.items()}


def write_document(path: Path, document: Mapping[str, Any]) -> None:
    """原子写出 JSON 对象（先临时文件再替换）。

    ``ensure_ascii=False`` + 缩进 2：这些文件是人机共编的，中文标题和路径要能直接读。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(dict(document), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
