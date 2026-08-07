"""工具系统规范化哈希 (ADR-0004 §12)

spec_hash / plan_hash / target_set_hash / catalog_snapshot_hash 是工具系统对外的稳定
锚点: 它们一变, ADR-0013 的风险缓存, always 学习规则和未完成审批就必须失效. 所以哈希
只能依赖值本身, 不能受字段声明顺序, 集合迭代顺序或进程启动次数影响.

不用 dataclasses.asdict: 它不认识 Enum 与 frozenset, 且会把派生字段 (如已算好的
spec_hash) 一起带进来, 让哈希自引用. 这里的 canonical() 显式处理每种形状, 派生字段由
各类型自己用 hash_source() 挑出源字段, 不走通用遍历.
"""

from __future__ import annotations
from collections.abc import Set, Mapping, Sequence
from dataclasses import fields, is_dataclass
from enum import Enum
import hashlib
import json

__all__ = ["canonical", "digest", "digest_text"]

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
        temp_list = []
        for key, val in value.items():
            temp_list.append((str(key), val))
        pairs = sorted(temp_list, key=_first)
        return {key: canonical(val) for key, val in pairs}
    if isinstance(value, Set):
        # 集合无序: 按各元素规范化后的 JSON 排序, 保证同一集合永远得到同一序列.
        return sorted((canonical(item) for item in value), key=_json_key)
    if isinstance(value, Sequence):
        return [canonical(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: canonical(getattr(value, f.name)) for f in fields(value)}
    raise TypeError(f"无法规范化的类型: {type(value).__name__}")

def digest(value: object) -> str:
    """规范化后取 sha256, 形如 ``sha256:ab12...``."""
    return digest_text(json.dumps(canonical(value), sort_keys=True, ensure_ascii=False))


def digest_text(text: str) -> str:
    """对一段已经规范化的文本取 sha256 (脚本内容, 命令原文等直接用它)."""
    return f"{_ALGORITHM}:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"

def _first(pair: tuple[str, object]) -> str:
    return pair[0]

def _json_key(item: object) -> str:
    return json.dumps(item, sort_keys=True, ensure_ascii=False)