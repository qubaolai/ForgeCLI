"""工作区边界判定（ADR-0009 决策 11）。

「这个路径在不在工作区内」是一条领域规则, 不是某个 service 的内部细节:
07-29 的规则引擎判区外读写、07-28 的命令解析器判 rm 的危险目标, 用的都是它。

纯函数, 不碰文件系统 —— 按**路径段**比较而非字符串前缀。前缀匹配会把 `/work-evil`
判成 `/work` 的区内路径, 那正是这类检查最容易被绕过的地方。symlink 逃逸不在这里防,
决策 11 把它归给结构化文件工具（需要 realpath, 那是 IO）。
"""

from __future__ import annotations

from pathlib import PurePath

__all__ = ["PATH_NORMALIZATION_VERSION", "is_within"]

PATH_NORMALIZATION_VERSION = "2"


def is_within(target: str | PurePath, root: str | PurePath) -> bool:
    """target 是否落在 root 之内（含 target 就是 root 自身）。"""
    target_path, root_path = PurePath(target), PurePath(root)
    return target_path == root_path or root_path in target_path.parents
