"""thinking 的会话内覆盖值（ADR-0011 §7）。

ThinkingOverride 描述这次会话希望把某模型的 thinking 调成什么, 是意图不是机制。
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.domain.model.thinking import (
    ThinkingEffortName,
    ThinkingMode,
)


@dataclass(frozen=True)
class ThinkingOverride:
    """一个模型的进程内部分覆盖；None 表示沿用 llm.json 默认值。"""

    mode: ThinkingMode | None = None
    effort: ThinkingEffortName | None = None
