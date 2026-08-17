"""prepare 阶段的结构化失败 (ADR-0004 §2).

prepare 失败直接回填模型, **不进入安全裁决** —— 入参根本没通过 schema 校验或路径根本
不存在时, 让规则引擎去裁决一个不成立的计划没有意义.

message 面向模型, 因此不得携带规划阶段读到的敏感内容 (凭证文件片段, 工作区外文件内容
等); 只说"哪一项不合法", 不回显读到了什么.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = ["PreparationError", "PreparationErrorCode"]


class PreparationErrorCode(Enum):
    INVALID_INPUT = "invalid_input"  # schema 校验不过
    TARGET_NOT_FOUND = "target_not_found"
    TARGET_UNREADABLE = "target_unreadable"
    UNSUPPORTED_REQUEST = "unsupported_request"
    CONTEXT_STALE = "context_stale"  # ExecutionContext 已变, 需要重算


@dataclass(frozen=True)
class PreparationError:
    code: PreparationErrorCode
    message: str
    field_path: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "type": "preparation_error",
            "code": self.code.value,
            "message": self.message,
            "field": self.field_path,
        }
