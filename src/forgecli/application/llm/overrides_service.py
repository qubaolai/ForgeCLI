"""ModelOverridesService：按用途模型覆盖的读取 / 校验 / 更新用例（ADR-0011 §16）。

规则（§5 /config 配置原则）：
    - 存储源是「origin -> provider/model」显式覆盖；每个 origin 最多一条。
    - 覆盖必须指向封闭注册表内的 provider 与 llm 配置中已声明的模型
      （目录校验的存在性部分；能力校验在调用时由 resolver 经 catalog 完成）。
    - 未配置覆盖的用途始终使用当前模型；不维护候选池 / 优先级 / 自动切换。
"""

from __future__ import annotations

from collections.abc import Mapping

from forgecli.application.llm import providers as provider_registry
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.llm.errors import ConfigValidationError
from forgecli.application.llm.gateway.errors import ModelBadRequestError
from forgecli.application.llm.overrides_store import ModelOverridesStore
from forgecli.domain.model.model_ref import ModelRef
from forgecli.domain.model.origin import RequestOrigin


class ModelOverridesService:
    """读取 / 校验 / 更新按用途覆盖；resolver 经 overrides() 现读。"""

    def __init__(
        self, store: ModelOverridesStore, llm_config_service: LlmConfigService
    ) -> None:
        self._store = store
        self._llm = llm_config_service

    def overrides(self) -> dict[RequestOrigin, ModelRef]:
        """类型化覆盖表（供 ModelSelectionResolver 注入）；非法项归一化报错。"""
        overrides: dict[RequestOrigin, ModelRef] = {}
        for raw_origin, body in self._store.load().items():
            try:
                origin = RequestOrigin(raw_origin)
            except ValueError:
                allowed = " / ".join(item.value for item in RequestOrigin)
                raise ModelBadRequestError(
                    f"未知用途覆盖 origin: {raw_origin!r}；只支持 [{allowed}]"
                ) from None
            if not isinstance(body, Mapping):
                raise ModelBadRequestError(
                    f"按用途覆盖 {raw_origin!r} 必须是 provider/model 键值表"
                )
            provider = str(body.get("provider", "")).strip()
            model = str(body.get("model", "")).strip()
            if not provider or not model:
                raise ModelBadRequestError(
                    f"按用途覆盖 {raw_origin!r} 必须同时指定非空 provider 与 model"
                )
            overrides[origin] = ModelRef(provider=provider, model=model)
        return overrides

    def override_for(self, origin: RequestOrigin) -> ModelRef | None:
        return self.overrides().get(origin)

    def set_override(self, origin: RequestOrigin, ref: ModelRef) -> None:
        """设置覆盖；provider 必须在封闭注册表内，模型必须已在 llm 配置声明。"""
        provider_registry.require_known_provider(ref.provider)
        if self._llm.config().model(ref.provider, ref.model) is None:
            raise ConfigValidationError(
                f"模型未在 LLM 配置中声明: {ref}（先在 /config 模型配置里添加）"
            )
        self._store.set_override(origin.value, ref.provider, ref.model)

    def clear_override(self, origin: RequestOrigin) -> None:
        self._store.clear_override(origin.value)
