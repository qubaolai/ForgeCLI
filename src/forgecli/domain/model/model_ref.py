"""稳定模型引用：provider + model。

ModelRef 只表示运行时默认模型指向哪个已配置模型。供应商和模型参数仍由
application/llm/config 读取 .forge/llm.json；默认引用由 application/config
读取 .forge/config.json。

InvalidModelRef 与 ModelRef 同住: 它守的就是本类的构造不变量, 且全仓只有这里抛。
它继承 shared.errors 的 ConfigValidationError —— domain 依赖 shared 是允许的,
shared 自身零依赖, 不会形成环。
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.shared.errors import ConfigValidationError

__all__ = ["InvalidModelRef", "ModelRef"]


class InvalidModelRef(ConfigValidationError):
    """模型引用非法（provider / name 为空等）。"""


@dataclass(frozen=True)
class ModelRef:
    """稳定模型引用，规范字符串形如 deepseek:deepseek-chat。"""

    provider: str
    model: str

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.model.strip():
            raise InvalidModelRef("模型引用的 provider 与 model 都不能为空")

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"
