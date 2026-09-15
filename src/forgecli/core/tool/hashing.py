"""跨模块契约锚点的规范化哈希 (ADR-0004 §12).

spec_hash / plan_hash / target_set_hash / catalog_snapshot_hash 是工具系统对外的稳定
锚点: 它们一变, ADR-0013 的风险缓存, always 学习规则和未完成审批就必须失效. 所以哈希
只能依赖值本身, 不能受字段声明顺序, 集合迭代顺序或进程启动次数影响.

不用 dataclasses.asdict: 它不认识 Enum 与 frozenset, 且会把派生字段 (如已算好的
spec_hash) 一起带进来, 让哈希自引用. 这里的 canonical() 显式处理每种形状, 派生字段由
各类型自己用 hash_source() 挑出源字段, 不走通用遍历.

`compare=False` 的字段一律不进哈希. 这条与"哈希只依赖值本身"是同一件事: 那些字段要么
是派生结果 (plan_hash, target_set_hash), 要么是"这一次调用的环境"而不是"这次调用是
什么" (filesystem_view_version 每次调用都换一个时间戳). 把后者算进去的后果是锚点永不
重复 —— 风险缓存永远命不中, always 学习规则永远匹配不上, 而这两件事都不会报错.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence, Set
from dataclasses import fields, is_dataclass
from enum import Enum

__all__ = ["canonical", "digest", "digest_bytes", "digest_text"]

_ALGORITHM = "sha256"


def canonical(value: object) -> object:
    """归一为只含 dict / list / str / int / float / bool / None 的可排序形状."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Enum):
        return canonical(value.value)
    if isinstance(value, bytes | bytearray):
        return bytes(value).hex()
    if isinstance(value, Mapping):
        pairs = sorted(((str(key), val) for key, val in value.items()), key=_first)
        return {key: canonical(val) for key, val in pairs}
    if isinstance(value, Set):
        # 集合无序: 按各元素规范化后的 JSON 排序, 保证同一集合永远得到同一序列.
        return sorted((canonical(item) for item in value), key=_json_key)
    if isinstance(value, Sequence):
        return [canonical(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: canonical(getattr(value, f.name))
            for f in fields(value)
            if f.compare
        }
    raise TypeError(f"无法规范化的类型: {type(value).__name__}")


def digest(value: object) -> str:
    """规范化后取 sha256, 形如 ``sha256:ab12...``."""
    return digest_text(json.dumps(canonical(value), sort_keys=True, ensure_ascii=False))


def digest_text(text: str) -> str:
    """对一段已经规范化的文本取 sha256 (脚本内容, 命令原文等直接用它)."""
    return f"{_ALGORITHM}:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def digest_bytes(data: bytes) -> str:
    """直接哈希字节，避免二进制先 decode 后把不同非法序列合并成同一替换字符。"""
    return f"{_ALGORITHM}:{hashlib.sha256(data).hexdigest()}"


def _first(pair: tuple[str, object]) -> str:
    return pair[0]


def _json_key(item: object) -> str:
    return json.dumps(item, sort_keys=True, ensure_ascii=False)
