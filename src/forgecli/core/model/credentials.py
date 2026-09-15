"""凭证值对象（ADR-0011 §6）。

Credential 的关键性质是不泄漏: value 字段 repr=False, __str__ 做掩码。这是凭证这个
概念自带的约束, 不随存储方式变化, 故属领域。解析与轮换是端口, 留在 application。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Credential:
    """一次调用可用的凭证。value 为 secret，绝不出现在 repr / str / 日志。"""

    provider_id: str
    ref: str
    value: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("Credential.provider_id 不能为空")
        if not self.ref.strip():
            raise ValueError("Credential.ref 不能为空")

    def __str__(self) -> str:
        # 只暴露 provider 与 ref（引用名本身不含 secret），value 一律掩码。
        return f"Credential({self.provider_id}, {self.ref}, ***)"
