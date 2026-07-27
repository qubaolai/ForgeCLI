"""模型级 thinking 能力与配置词汇。

thinking mode 是 ForgeCLI 的统一控制语义；effort 名称由具体模型声明，
不使用全局封闭枚举。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

_EFFORT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")


class ThinkingMode(Enum):
    """模型思考开关。"""

    ON = "on"
    OFF = "off"


@dataclass(frozen=True, order=True)
class ThinkingEffortName:
    """模型声明的思考强度名称，例如 low、max、xhigh。"""

    value: str

    def __post_init__(self) -> None:
        if not _EFFORT_NAME_PATTERN.fullmatch(self.value):
            raise ValueError(
                "thinking effort 必须是小写标识符，" f"收到: {self.value!r}"
            )

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class ModelThinkingSettings:
    """用户为一个具体模型选择的 thinking 设置。"""

    mode: ThinkingMode = ThinkingMode.OFF
    effort: ThinkingEffortName | None = None


@dataclass(frozen=True)
class ModelThinkingCapabilities:
    """一个具体模型声明的 thinking 强度选项。"""

    efforts: tuple[ThinkingEffortName, ...] = ()
    default_effort: ThinkingEffortName | None = None

    def __post_init__(self) -> None:
        if len(set(self.efforts)) != len(self.efforts):
            raise ValueError("thinking efforts 不能包含重复值")

        if self.default_effort is not None and self.default_effort not in self.efforts:
            raise ValueError("default_effort 必须包含在 efforts 中")

    def validate(self, settings: ModelThinkingSettings) -> None:
        """校验模型设置是否符合该模型的能力声明。"""

        if settings.effort is None:
            return

        if settings.effort not in self.efforts:
            allowed = " / ".join(str(item) for item in self.efforts)
            raise ValueError(
                f"该模型不支持 thinking effort {settings.effort!s}；"
                f"可选值 [{allowed}]"
            )

    def effective_effort(
        self, settings: ModelThinkingSettings
    ) -> ThinkingEffortName | None:
        """显式设置优先，否则使用模型默认强度。"""

        self.validate(settings)
        return settings.effort or self.default_effort
