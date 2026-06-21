"""LLM 配置持久化端口（由 infrastructure 用 tomlkit 实现）。

约定：
    - load() 返回 [llm] 段落的原始嵌套 dict（plain python），无文件返回 {}。
    - load() 解析失败抛 ConfigReadError（已翻成用户可理解的错误）。
    - upsert_model() / remove_model() / upsert_provider_field() 做 round-trip 写入：
      保留文件里其余内容与注释。
    - store 不做供应商白名单 / 参数校验——那是 application 的封闭职责。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping


class LlmConfigStore(ABC):
    @abstractmethod
    def load(self) -> dict[str, object]:
        """读取 [llm] 段为嵌套 dict；无文件返回 {}，解析失败抛 ConfigReadError。"""

    @abstractmethod
    def upsert_model(
        self,
        provider_id: str,
        model_id: str,
        fields: Mapping[str, object],
        *,
        provider_defaults: Mapping[str, object],
    ) -> None:
        """新增 / 覆盖一个模型；provider 段不存在则用默认建立（round-trip）。"""

    @abstractmethod
    def remove_model(self, provider_id: str, model_id: str) -> None:
        """删除一个模型；不存在时静默。round-trip 写。"""

    @abstractmethod
    def upsert_provider_field(
        self,
        provider_id: str,
        field: str,
        value: object,
        *,
        provider_defaults: Mapping[str, object],
    ) -> None:
        """设置供应商顶层字段(name/api_base 等)；段不存在则用默认建立（round-trip）。"""
