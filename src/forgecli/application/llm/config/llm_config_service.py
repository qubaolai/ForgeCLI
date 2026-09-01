"""LlmConfigService：读取 / 校验 / 更新 LLM 配置的用例（配置切片）。

职责边界：
    - config():        把文件里的 [llm] 段解析成不可变 LlmConfig（含校验）。
    - providers():     有效供应商列表（只含命中注册表的）。
    - add_model():     校验供应商（封闭）与参数后 round-trip 写入。
    - remove_model():  删除一个模型。
    - set_model_field / set_model_extra / set_provider_field：逐项编辑（读-改-写）。
    - cache_settings / circuit_breaker_settings / retry_settings：ADR-0012 新增
      运行时配置段的现读解析，缺省时返回默认值（全部默认关闭 / 回落现行为），
      wiring 据此装配治理件。已移除：rate_limit_settings（客户端主动限流，见
      gateway/governance.py）、credential_settings（凭证只支持环境变量，无
      dotenv 开关，见 infrastructure/llm/credentials.py）。

读取时遇到未知供应商段：忽略（没有 adapter 可驱动），不报错。
写入时显式引用未知供应商：抛 UnknownProvider（封闭集合不可新增）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from pydantic import ValidationError

from forgecli.application.llm import providers as provider_registry
from forgecli.application.llm.catalog_builder import build_catalog
from forgecli.application.llm.config.llm_config import (
    CacheSettings,
    CircuitBreakerSettings,
    LlmConfig,
    ModelParams,
    ModelSpec,
    ProviderConfig,
    RetrySettings,
    coerce_field,
    coerce_provider_field,
    explain_validation_error,
    parse_extra,
)
from forgecli.application.llm.config.llm_config_store import LlmConfigStore
from forgecli.application.llm.errors import ConfigValidationError
from forgecli.domain.model.catalog import ModelCatalogEntry
from forgecli.domain.model.model_ref import ModelRef
from forgecli.domain.model.origin import RequestOrigin
from forgecli.domain.model.provider_spec import ProviderProtocol, ProviderSpec
from forgecli.domain.model.thinking import (
    ModelThinkingCapabilities,
    ThinkingEffortName,
    ThinkingMode,
)


def _as_bool(name: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise ConfigValidationError(f"{name} 必须是布尔值，收到: {value!r}")
    return value


def _as_bool_text(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigValidationError(f"{name} 必须是 true/false，收到: {value!r}")


def _as_positive_int_value(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigValidationError(f"{name} 必须是整数，收到: {value!r}")
    if value <= 0:
        raise ConfigValidationError(f"{name} 必须为正整数，收到: {value!r}")
    return value


def _as_positive_number(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigValidationError(f"{name} 必须是数字，收到: {value!r}")
    number = float(value)
    if number <= 0:
        raise ConfigValidationError(f"{name} 必须为正数，收到: {value!r}")
    return number


def _as_nonneg_number(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigValidationError(f"{name} 必须是数字，收到: {value!r}")
    number = float(value)
    if number < 0:
        raise ConfigValidationError(f"{name} 不能为负，收到: {value!r}")
    return number


def _as_origins(
    value: object, defaults: tuple[RequestOrigin, ...]
) -> tuple[RequestOrigin, ...]:
    """解析 origins 白名单；未知 origin 字符串直接报错（封闭枚举）。"""
    if value is None:
        return defaults
    if not isinstance(value, list | tuple) or not all(
        isinstance(item, str) for item in value
    ):
        raise ConfigValidationError(f"origins 必须是字符串数组，收到: {value!r}")
    origins: list[RequestOrigin] = []
    for item in value:
        try:
            origins.append(RequestOrigin(item))
        except ValueError:
            allowed = " / ".join(origin.value for origin in RequestOrigin)
            raise ConfigValidationError(
                f"未知 origin: {item!r}；可选值 [{allowed}]"
            ) from None
    return tuple(origins)


def _merge_provider_sections(
    section: Mapping[str, object],
) -> dict[str, dict[str, object]]:
    """按归一后的 id 把供应商段落合成一份, 未知供应商丢掉.

    为什么会有两个段落: 注册表的 key 曾经是 `"GLM"`, 早期版本的菜单照着它写出了
    `providers.GLM`. key 改回 `"glm"` 之后, 新的写入落在 `providers.glm` 上, 于是同一
    家供应商在文件里有了两份. 不合并的话 `LlmConfig.provider()` 只会命中先出现的那一份,
    另一份里的模型就成了"存进去了但找不到"。

    合并规则是**后面的段落覆盖前面的同名键**, 模型按 id 取并集. 一个键只有在有人往那个
    段落里写过的时候才会出现, 所以"后写的赢"就是"最近一次编辑赢".

    未知供应商在这里就丢掉: 没有 adapter 能驱动它们, 留着只会让下游每一处都要再判一次.
    """
    merged: dict[str, dict[str, object]] = {}
    for raw_id, body in section.items():
        provider_id = provider_registry.normalize_provider_id(str(raw_id))
        if not provider_registry.is_known_provider(provider_id) or not isinstance(
            body, Mapping
        ):
            continue
        target = merged.setdefault(provider_id, {})
        carried = target.get("models")
        models: dict[str, object] = (
            dict(carried) if isinstance(carried, Mapping) else {}
        )
        for key, value in body.items():
            if key != "models":
                target[key] = value
        incoming = body.get("models")
        if isinstance(incoming, Mapping):
            models.update(incoming)
        target["models"] = models
    return merged


class LlmConfigService:
    """读取 / 校验 / 更新 LLM 配置的用例；只有一个实现，故不设抽象基类。"""

    def __init__(self, store: LlmConfigStore) -> None:
        self._store = store

    def config(self) -> LlmConfig:
        section = self._store.load().get("providers", {})
        if not isinstance(section, Mapping):
            return LlmConfig(providers=())
        # 登记必须在合并之前: `_merge_provider_sections` 会丢掉"未知供应商", 而用户
        # 自建的那几家在登记之前正是未知的 —— 顺序反了就永远读不回来.
        _register_user_providers(section)
        merged = _merge_provider_sections(section)
        return LlmConfig(
            providers=tuple(
                self._parse_provider(provider_id, body)
                for provider_id, body in merged.items()
            )
        )

    def providers(self) -> tuple[ProviderConfig, ...]:
        return self.config().providers

    def effective_providers(self) -> tuple[ProviderConfig, ...]:
        """每一家内置供应商各一条: 配置里写了的用配置, 没写的用注册表默认值.

        `providers()` 只返回配置文件里真的有段落的那些, 因为装配 adapter 只关心它们.
        设置页要的是另一件事: 用户得能在**添加第一个模型之前**就把端点和密钥变量名填好,
        而那时候配置文件里还没有这家供应商的段落 —— 它在 `providers()` 里根本不存在,
        于是界面上也就没有一行可以编辑.
        """
        configured = {provider.id: provider for provider in self.providers()}
        # 内置的都要有一行 (哪怕还没配过); 用户自建的只有配过才存在, 所以直接取配置里
        # 那几条 —— 它们没有"注册表默认值"可以回落.
        listed = sorted({*provider_registry.REGISTRY, *configured})
        return tuple(
            configured.get(provider_id) or self._parse_provider(provider_id, {})
            for provider_id in listed
        )

    def add_provider(
        self,
        provider_id: str,
        *,
        name: str,
        api_base: str,
        protocol: str,
        api_key_env: str = "",
    ) -> None:
        """加一家用户自建的供应商.

        只收讲 OpenAI 兼容协议的: 另外两种协议的 adapter 还不存在, 让它存进配置只会把
        失败推迟到第一次真正调用 —— 那时用户已经填完所有字段并选好了模型.
        """
        parsed = _require_supported_protocol(protocol)
        provider_id = provider_registry.normalize_provider_id(provider_id)
        if not provider_id:
            raise ConfigValidationError("供应商 id 不能为空")
        if provider_id in provider_registry.REGISTRY:
            raise ConfigValidationError(f"{provider_id} 是内置供应商, 直接编辑它即可")
        if not api_base.strip():
            raise ConfigValidationError("API 地址不能为空")
        provider_registry.register_user_provider(
            ProviderSpec(
                id=provider_id,
                label=name.strip() or provider_id,
                default_api_base=api_base.strip(),
                api_key_env=api_key_env.strip(),
                protocol=parsed,
            )
        )
        # 借 upsert_provider_field 的"段不存在就按默认建立"把四个字段一次写进去:
        # 逐字段写会在中途留下一个只有半截的段落, 而那一刻崩了就是一家配不出请求的
        # 供应商躺在文件里.
        defaults = {
            "name": name.strip() or provider_id,
            "api_base": api_base.strip(),
            "api_key_env": api_key_env.strip(),
            "protocol": parsed.value,
        }
        self._store.upsert_provider_field(
            provider_id, "protocol", parsed.value, provider_defaults=defaults
        )

    def add_model(
        self, provider_id: str, model_id: str, params: Mapping[str, object]
    ) -> None:
        provider_id = self._canonical(provider_id)
        if not model_id.strip():
            raise ConfigValidationError("模型 id 不能为空")
        # 用同一套校验，保证菜单写入与手改文件得到一致约束。
        parsed = ModelParams.parse(params)
        self._store.upsert_model(
            provider_id,
            model_id,
            parsed.to_fields(),
            provider_defaults=self._defaults(provider_id),
        )

    def remove_model(self, provider_id: str, model_id: str) -> None:
        self._store.remove_model(self._canonical(provider_id), model_id)

    def update_model_thinking(
        self,
        provider_id: str,
        model_id: str,
        *,
        mode: ThinkingMode | None = None,
        effort: ThinkingEffortName | None = None,
    ) -> bool:
        """原子更新一个模型的 thinking 设置。

        mode/effort 均为 None 时不写入。mode 直接控制 thinking；mode=off 只关闭
        使用并保留强度配置。候选配置会在唯一一次持久化前完整校验。
        返回配置文件是否实际发生变化。
        """
        provider_id = self._canonical(provider_id)
        fields = self._existing_fields(provider_id, model_id)
        before = dict(fields)
        if mode is not None:
            fields["thinking_mode"] = mode.value
        if effort is not None:
            fields["thinking_effort"] = effort.value
        return self._write_model_fields(provider_id, model_id, before, fields)

    def set_model_thinking_efforts(
        self, provider_id: str, model_id: str, raw: str
    ) -> bool:
        """以逗号分隔文本设置模型支持的开放 effort 集合。"""
        provider_id = self._canonical(provider_id)
        values = tuple(item.strip().lower() for item in raw.split(",") if item.strip())
        try:
            efforts = tuple(ThinkingEffortName(item) for item in values)
            # 复用值对象做重复值等能力不变量校验。
            ModelThinkingCapabilities(efforts=efforts)
        except ValueError as exc:
            raise ConfigValidationError(str(exc)) from exc

        fields = self._existing_fields(provider_id, model_id)
        before = dict(fields)
        fields["thinking_efforts"] = [item.value for item in efforts]

        selected = fields.get("thinking_effort")
        if selected not in values:
            fields.pop("thinking_effort", None)
        configured_default = fields.get("thinking_default_effort")
        if configured_default not in values:
            fields.pop("thinking_default_effort", None)
        return self._write_model_fields(provider_id, model_id, before, fields)

    def set_model_thinking_default_effort(
        self, provider_id: str, model_id: str, raw: str
    ) -> bool:
        """设置默认 effort；留空表示该模型不配置默认强度。"""
        provider_id = self._canonical(provider_id)
        entry = self._thinking_entry(provider_id, model_id)
        text = raw.strip().lower()
        fields = self._existing_fields(provider_id, model_id)
        before = dict(fields)
        if not text:
            fields.pop("thinking_default_effort", None)
        else:
            try:
                effort = ThinkingEffortName(text)
            except ValueError as exc:
                raise ConfigValidationError(str(exc)) from exc
            if effort not in entry.thinking_capabilities.efforts:
                allowed = " / ".join(
                    item.value for item in entry.thinking_capabilities.efforts
                )
                raise ConfigValidationError(
                    f"模型 {provider_id}:{model_id} 不支持默认强度 {text!r}；"
                    f"可选值 [{allowed}]"
                )
            fields["thinking_efforts"] = [
                item.value for item in entry.thinking_capabilities.efforts
            ]
            fields["thinking_default_effort"] = effort.value
        return self._write_model_fields(provider_id, model_id, before, fields)

    def set_model_field(
        self, provider_id: str, model_id: str, field: str, raw: str
    ) -> None:
        provider_id = self._canonical(provider_id)
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
        provider_id = self._canonical(provider_id)
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
        provider_id = self._canonical(provider_id)
        value = coerce_provider_field(field, raw)
        self._store.upsert_provider_field(
            provider_id, field, value, provider_defaults=self._defaults(provider_id)
        )

    # ---- 网关运行时配置段（ADR-0012）----

    def cache_settings(self) -> CacheSettings:
        """[llm.cache]（ADR-0012 §3）；缺省返回默认（关闭）。"""
        section = self._section("cache")
        defaults = CacheSettings()
        ttl_raw = section.get("ttl_seconds", defaults.ttl_seconds)
        ttl = None if ttl_raw is None else _as_positive_number("ttl_seconds", ttl_raw)
        return CacheSettings(
            enabled=_as_bool("enabled", section.get("enabled", defaults.enabled)),
            ttl_seconds=ttl,
            max_entries=_as_positive_int_value(
                "max_entries", section.get("max_entries", defaults.max_entries)
            ),
            origins=_as_origins(section.get("origins"), defaults.origins),
        )

    def circuit_breaker_settings(self) -> CircuitBreakerSettings:
        """[llm.circuit_breaker]（ADR-0012 §8）；缺省返回默认（关闭）。"""
        section = self._section("circuit_breaker")
        defaults = CircuitBreakerSettings()
        return CircuitBreakerSettings(
            enabled=_as_bool("enabled", section.get("enabled", defaults.enabled)),
            failure_threshold=_as_positive_int_value(
                "failure_threshold",
                section.get("failure_threshold", defaults.failure_threshold),
            ),
            cooldown_seconds=_as_positive_number(
                "cooldown_seconds",
                section.get("cooldown_seconds", defaults.cooldown_seconds),
            ),
        )

    def retry_settings(self) -> RetrySettings:
        """[llm.retry]（ADR-0012 §2）；缺省阈值 5.0 秒。"""
        section = self._section("retry")
        defaults = RetrySettings()
        return RetrySettings(
            wait_threshold_seconds=_as_nonneg_number(
                "wait_threshold_seconds",
                section.get("wait_threshold_seconds", defaults.wait_threshold_seconds),
            ),
        )

    def runtime_settings_snapshot(
        self,
    ) -> tuple[CacheSettings, CircuitBreakerSettings, RetrySettings]:
        """网关应用级运行时配置快照，用于 /config 变更检测。"""
        return (
            self.cache_settings(),
            self.circuit_breaker_settings(),
            self.retry_settings(),
        )

    def set_runtime_field(self, section: str, field: str, raw: str) -> None:
        """校验并写入应用级 [llm.cache/circuit_breaker/retry] 字段。

        这是 /config 的唯一写入用例；CLI 只提交文本，不解析 TOML。
        """
        key = (section, field)
        value: object
        if key in {("cache", "enabled"), ("circuit_breaker", "enabled")}:
            value = _as_bool_text(f"{section}.{field}", raw)
        elif key in {
            ("cache", "max_entries"),
            ("circuit_breaker", "failure_threshold"),
        }:
            try:
                parsed = int(raw.strip())
            except ValueError:
                raise ConfigValidationError(
                    f"{section}.{field} 必须是整数，收到: {raw!r}"
                ) from None
            value = _as_positive_int_value(f"{section}.{field}", parsed)
        elif key in {
            ("cache", "ttl_seconds"),
            ("circuit_breaker", "cooldown_seconds"),
        }:
            try:
                parsed_number = float(raw.strip())
            except ValueError:
                raise ConfigValidationError(
                    f"{section}.{field} 必须是数字，收到: {raw!r}"
                ) from None
            value = _as_positive_number(f"{section}.{field}", parsed_number)
        elif key == ("retry", "wait_threshold_seconds"):
            try:
                parsed_number = float(raw.strip())
            except ValueError:
                raise ConfigValidationError(
                    f"{section}.{field} 必须是数字，收到: {raw!r}"
                ) from None
            value = _as_nonneg_number(f"{section}.{field}", parsed_number)
        elif key == ("cache", "origins"):
            names = [item.strip() for item in raw.split(",") if item.strip()]
            value = [origin.value for origin in _as_origins(names, ())]
        else:
            raise ConfigValidationError(f"未知网关运行时配置项: {section}.{field}")
        self._store.upsert_runtime_field(section, field, value)

    def _section(self, name: str) -> Mapping[str, object]:
        section = self._store.load().get(name, {})
        if not isinstance(section, Mapping):
            raise ConfigValidationError(f"[llm.{name}] 必须是一个配置段")
        return section

    # ---- 解析 ----

    @staticmethod
    def _canonical(provider_id: str) -> str:
        """校验并换成注册表的 key.

        **每一个写入口都要过这里.** 归一只做在读那一侧的话, 会写出第二个段落: 页面上
        看到的 id 是归一后的 `glm`, 而文件里躺着的是早期版本写的 `GLM` —— 于是
        `upsert_model("glm", ...)` 新建一个 `glm` 段, 同一家供应商在文件里有了两份,
        往后每次查找都只命中先出现的那一份, 新加的模型"存进去了但找不到".
        """
        return provider_registry.require_known_provider(provider_id).id

    def _defaults(self, provider_id: str) -> dict[str, object]:
        """新建供应商段落时填什么.

        已经配过这家供应商就用它现在的有效值, 而不是注册表默认值: 用户改过的 api_base
        不该因为"又加了一个模型"被默认值盖回去.
        """
        spec = provider_registry.require_known_provider(provider_id)
        current = self.config().provider(spec.id)
        if current is not None:
            return {
                "name": current.name,
                "api_base": current.api_base,
                "api_key_env": current.api_key_env or "",
            }
        return {
            "name": spec.label,
            "api_base": spec.default_api_base,
            "api_key_env": spec.api_key_env,
        }

    def _existing_fields(self, provider_id: str, model_id: str) -> dict[str, object]:
        provider_id = self._canonical(provider_id)
        model = self.config().model(provider_id, model_id)
        if model is None:
            raise ConfigValidationError(f"模型不存在: {provider_id}/{model_id}")
        return model.params.to_fields()

    def _thinking_entry(self, provider_id: str, model_id: str) -> ModelCatalogEntry:
        ref = ModelRef(provider=provider_id, model=model_id)
        catalog = build_catalog(self.config())
        if not catalog.has_model(ref):
            raise ConfigValidationError(f"模型不存在: {ref}")
        return catalog.get(ref)

    def _write_model_fields(
        self,
        provider_id: str,
        model_id: str,
        before: Mapping[str, object],
        fields: Mapping[str, object],
    ) -> bool:
        provider_id = self._canonical(provider_id)
        if fields == before:
            return False
        parsed = ModelParams.parse(fields)
        config = self.config()
        provider = config.provider(provider_id)
        if provider is None:
            raise ConfigValidationError(f"供应商不存在: {provider_id}")
        replacement = ModelSpec(provider=provider_id, id=model_id, params=parsed)
        models = tuple(
            replacement if model.id == model_id else model for model in provider.models
        )
        # ProviderConfig 已从 dataclass 迁移为 frozen Pydantic model；这里如果继续用
        # dataclasses.replace，Thinking / effort 的编辑会在落盘前直接抛 TypeError，
        # Web 层最终只能返回一个没有业务信息的 422。
        candidate_provider = provider.model_copy(update={"models": models})
        candidate = replace(
            config,
            providers=tuple(
                candidate_provider if item.id == provider_id else item
                for item in config.providers
            ),
        )
        # 先在内存候选配置上完成 catalog 合并校验，再进行唯一一次持久化写入。
        build_catalog(candidate)
        self._store.upsert_model(
            provider_id,
            model_id,
            parsed.to_fields(),
            provider_defaults=self._defaults(provider_id),
        )
        return True

    def _parse_provider(
        self, provider_id: str, body: Mapping[str, object]
    ) -> ProviderConfig:
        spec = provider_registry.require_known_provider(provider_id)
        raw_models = body.get("models", {})
        models: list[ModelSpec] = []
        if isinstance(raw_models, Mapping):
            for model_id, model_body in raw_models.items():
                model_fields = model_body if isinstance(model_body, Mapping) else {}
                models.append(
                    ModelSpec(
                        provider=provider_id,
                        id=model_id,
                        params=ModelParams.parse(model_fields),
                    )
                )
        api_key_env = body.get("api_key_env", spec.api_key_env)
        # 注册表给的是**运行时**默认值 (这家供应商叫什么, 官方地址是哪个), 所以只能在
        # 这里补, 不能写成 ProviderConfig 的字段默认值 —— 那样每家都会拿到同一份.
        provider_fields: dict[str, object] = {
            "id": provider_id,
            "name": body.get("name", spec.label),
            "api_base": body.get("api_base", spec.default_api_base),
            "api_key_env": str(api_key_env) if api_key_env else None,
            "protocol": str(body.get("protocol", spec.protocol.value)),
            "models": tuple(models),
        }
        for optional in ("timeout", "max_retries", "credential_refs"):
            if optional in body:
                provider_fields[optional] = body[optional]
        try:
            return ProviderConfig.model_validate(provider_fields)
        except ValidationError as exc:
            raise ConfigValidationError(explain_validation_error(exc)) from exc


def _register_user_providers(section: Mapping[str, object]) -> None:
    """把配置里非内置的那几家登记回注册表.

    每次读配置都跑一遍, 而不是只在 `add_provider` 时登记一次: 登记住在进程内存里, 重启
    之后就没了, 而配置文件还在 —— 少了这一步, 用户加的供应商能写进文件, 重启之后
    `require_known_provider` 却说它不存在, 而那时模型已经配在它下面了.

    直接读**原始段落**而不是解析后的 ProviderConfig: 解析那一步要先认得这家供应商, 而
    认得它正是这里要建立的前提.
    """
    provider_registry.forget_user_providers()
    for raw_id, body in section.items():
        provider_id = provider_registry.normalize_provider_id(str(raw_id))
        if provider_id in provider_registry.REGISTRY or not isinstance(body, Mapping):
            continue
        api_base = str(body.get("api_base", "")).strip()
        if not api_base:
            # 没有端点就发不出请求. 登记它只会让它出现在列表里然后每次调用都失败.
            continue
        try:
            protocol = ProviderProtocol(str(body.get("protocol", "")).strip())
        except ValueError:
            # 手改配置写了个认不出的协议. 跳过它而不是抛 —— 抛会让整个配置读不出来,
            # 而后果是所有供应商一起消失, 包括内置的.
            continue
        provider_registry.register_user_provider(
            ProviderSpec(
                id=provider_id,
                label=str(body.get("name", "")).strip() or provider_id,
                default_api_base=api_base,
                api_key_env=str(body.get("api_key_env", "")).strip(),
                protocol=protocol,
            )
        )


def _require_supported_protocol(raw: str) -> ProviderProtocol:
    """认协议名, 并挡住还没有 adapter 的那两种.

    分两句话说: "不认识这个名字"和"认识但还不支持"要给出不同的提示 —— 后者是用户看着
    界面上那一项选的, 告诉他"未知协议"只会让他以为自己填错了.
    """
    try:
        protocol = ProviderProtocol(raw.strip())
    except ValueError:
        allowed = " / ".join(item.value for item in ProviderProtocol)
        raise ConfigValidationError(
            f"未知协议 {raw!r}; 取值只能是 [{allowed}]"
        ) from None
    if not protocol.supported:
        raise ConfigValidationError(
            f"{protocol.label} 目前还没有对应的 adapter, 暂时不能添加"
        )
    return protocol
