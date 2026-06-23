"""LlmConfigService：读取 / 校验 / 更新 LLM 配置的用例（配置切片）。

职责边界：
    - config():        把文件里的 [llm] 段解析成不可变 LlmConfig（含校验）。
    - providers():     有效供应商列表（只含命中注册表的）。
    - add_model():     校验供应商（封闭）与参数后 round-trip 写入。
    - remove_model():  删除一个模型。
    - set_model_field / set_model_extra / set_provider_field：逐项编辑（读-改-写）。

读取时遇到未知供应商段：忽略（没有 adapter 可驱动），不报错。
写入时显式引用未知供应商：抛 UnknownProvider（封闭集合不可新增）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping

from forgecli.application.llm import providers as provider_registry
from forgecli.application.llm.config.llm_config import (
    LlmConfig,
    ModelParams,
    ModelSpec,
    ProviderConfig,
    coerce_field,
    parse_extra,
)
from forgecli.application.llm.config.llm_config_store import LlmConfigStore
from forgecli.application.llm.errors import ConfigValidationError

_DEFAULT_TIMEOUT = 60
_DEFAULT_MAX_RETRIES = 2

# 供应商可在菜单里编辑的字段及类型
_PROVIDER_STR_FIELDS = {"name", "api_base", "api_key_env"}
_PROVIDER_INT_FIELDS = {"timeout", "max_retries"}


def _as_int(name: str, value: object, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigValidationError(f"{name} 必须是整数，收到: {value!r}")
    return value


class LlmConfigService(ABC):
    @abstractmethod
    def config(self) -> LlmConfig: ...

    @abstractmethod
    def providers(self) -> tuple[ProviderConfig, ...]: ...

    @abstractmethod
    def add_model(
        self, provider_id: str, model_id: str, params: Mapping[str, object]
    ) -> None: ...

    @abstractmethod
    def remove_model(self, provider_id: str, model_id: str) -> None: ...

    @abstractmethod
    def set_model_field(
        self, provider_id: str, model_id: str, field: str, raw: str
    ) -> None: ...

    @abstractmethod
    def set_model_extra(
        self, provider_id: str, model_id: str, raw_json: str
    ) -> None: ...

    @abstractmethod
    def set_provider_field(self, provider_id: str, field: str, raw: str) -> None: ...


class FileLlmConfigService(LlmConfigService):
    def __init__(self, store: LlmConfigStore) -> None:
        self._store = store

    def config(self) -> LlmConfig:
        section = self._store.load().get("providers", {})
        providers: list[ProviderConfig] = []
        if isinstance(section, Mapping):
            for provider_id, body in section.items():
                # 未知供应商：没有 adapter 能驱动，忽略而非报错。
                if not provider_registry.is_known_provider(provider_id):
                    continue
                if not isinstance(body, Mapping):
                    continue
                providers.append(self._parse_provider(provider_id, body))
        return LlmConfig(providers=tuple(providers))

    def providers(self) -> tuple[ProviderConfig, ...]:
        return self.config().providers

    def add_model(
        self, provider_id: str, model_id: str, params: Mapping[str, object]
    ) -> None:
        provider_registry.require_known_provider(provider_id)
        if not model_id.strip():
            raise ConfigValidationError("模型 id 不能为空")
        # 用同一套校验，保证菜单写入与手改文件得到一致约束。
        ModelParams.parse(params)
        self._store.upsert_model(
            provider_id,
            model_id,
            params,
            provider_defaults=self._defaults(provider_id),
        )

    def remove_model(self, provider_id: str, model_id: str) -> None:
        provider_registry.require_known_provider(provider_id)
        self._store.remove_model(provider_id, model_id)

    def set_model_field(
        self, provider_id: str, model_id: str, field: str, raw: str
    ) -> None:
        fields = self._existing_fields(provider_id, model_id)
        text = raw.strip()
        if text == "":
            fields.pop(field, None)  # 留空即清除该可选字段
        else:
            fields[field] = coerce_field(field, text)
        ModelParams.parse(fields)  # 范围校验
        self._store.upsert_model(
            provider_id, model_id, fields, provider_defaults=self._defaults(provider_id)
        )

    def set_model_extra(self, provider_id: str, model_id: str, raw_json: str) -> None:
        fields = self._existing_fields(provider_id, model_id)
        extra = parse_extra(raw_json)
        if extra:
            fields["extra"] = extra
        else:
            fields.pop("extra", None)
        ModelParams.parse(fields)
        self._store.upsert_model(
            provider_id, model_id, fields, provider_defaults=self._defaults(provider_id)
        )

    def set_provider_field(self, provider_id: str, field: str, raw: str) -> None:
        provider_registry.require_known_provider(provider_id)
        text = raw.strip()
        value: object
        if field in _PROVIDER_INT_FIELDS:
            try:
                number = int(text)
            except ValueError:
                raise ConfigValidationError(
                    f"{field} 必须是整数，收到: {raw!r}"
                ) from None
            if number < 0:
                raise ConfigValidationError(f"{field} 不能为负")
            value = number
        elif field in _PROVIDER_STR_FIELDS:
            if not text:
                raise ConfigValidationError(f"{field} 不能为空")
            value = text
        else:
            raise ConfigValidationError(f"未知供应商字段: {field}")
        self._store.upsert_provider_field(
            provider_id, field, value, provider_defaults=self._defaults(provider_id)
        )

    # ---- 解析 ----

    def _defaults(self, provider_id: str) -> dict[str, object]:
        spec = provider_registry.REGISTRY[provider_id]
        return {
            "name": spec.label,
            "api_base": spec.default_api_base,
            "api_key_env": spec.api_key_env,
        }

    def _existing_fields(self, provider_id: str, model_id: str) -> dict[str, object]:
        provider_registry.require_known_provider(provider_id)
        model = self.config().model(provider_id, model_id)
        if model is None:
            raise ConfigValidationError(f"模型不存在: {provider_id}/{model_id}")
        return model.params.to_fields()

    def _parse_provider(
        self, provider_id: str, body: Mapping[str, object]
    ) -> ProviderConfig:
        spec = provider_registry.REGISTRY[provider_id]
        raw_models = body.get("models", {})
        models: list[ModelSpec] = []
        if isinstance(raw_models, Mapping):
            for model_id, model_body in raw_models.items():
                fields = model_body if isinstance(model_body, Mapping) else {}
                models.append(
                    ModelSpec(
                        provider=provider_id,
                        id=model_id,
                        params=ModelParams.parse(fields),
                    )
                )
        api_key_env = body.get("api_key_env", spec.api_key_env)
        return ProviderConfig(
            id=provider_id,
            name=str(body.get("name", spec.label)),
            api_base=str(body.get("api_base", spec.default_api_base)),
            api_key_env=str(api_key_env) if api_key_env else None,
            timeout=_as_int("timeout", body.get("timeout"), _DEFAULT_TIMEOUT),
            max_retries=_as_int(
                "max_retries", body.get("max_retries"), _DEFAULT_MAX_RETRIES
            ),
            models=tuple(models),
        )
