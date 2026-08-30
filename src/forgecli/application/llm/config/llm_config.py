"""LLM 配置的不可变值对象与模型参数解析 / 校验（配置切片与调用切片共享词汇）。

层次：
    LlmConfig             整份有效配置（多家供应商）
      └ ProviderConfig    一家供应商（元数据 + 其模型）
          └ ModelSpec     一个模型（标识 + 参数）
              └ ModelParams  模型参数：能力/计费元数据 + 标准超参 + 厂商自定义(extra)

参数分两类（对齐需求）：
    - 标准化字段：context_window / max_tokens / cost_* / temperature / top_p，
      带类型与范围校验。
    - 厂商自定义：extra，任意 JSON object，原样透传，只校验「是个对象」。

这些值对象将来由调用切片（adapter）直接消费来构造请求。它们也是 /model 面板选择
运行时默认模型时的唯一模型来源。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    PlainSerializer,
    ValidationError,
    field_validator,
    model_validator,
)

from forgecli.application.llm.errors import ConfigValidationError
from forgecli.domain.model.origin import RequestOrigin
from forgecli.domain.model.thinking import (
    ModelThinkingCapabilities,
    ModelThinkingSettings,
    ThinkingEffortName,
    ThinkingMode,
)

# Pydantic 的错误类型 -> 一句用户读得懂的中文.
#
# 为什么要这张表: ValidationError 的原文是英文的库内部措辞 ("Input should be a valid
# integer"), 而这条错误会直接出现在 /config 菜单里. ADR-0040 决策 4.2 写明不把
# ValidationError 原样展示给最终用户.
_REASONS: dict[str, str] = {
    "int_type": "必须是整数",
    "float_type": "必须是数字",
    "string_type": "必须是字符串",
    "greater_than": "必须大于 {gt}",
    "greater_than_equal": "不能小于 {ge}",
    "less_than_equal": "不能大于 {le}",
    "enum": "取值不在允许的范围内",
    "extra_forbidden": "不是可识别的字段",
    "tuple_type": "必须是一个列表",
    "list_type": "必须是一个列表",
    "mapping_type": "必须是 JSON 对象（键值表）",
}


def explain_validation_error(error: ValidationError) -> str:
    """把 ValidationError 翻成一行中文, 只讲第一条 —— 用户一次改一个字段."""
    first = error.errors()[0]
    where = ".".join(str(part) for part in first["loc"]) or "配置"
    if first["type"] == "value_error":
        # 自己的校验器抛的 ValueError: 消息本来就是给用户看的中文, 只是被 Pydantic
        # 加了个英文前缀. 去掉前缀原样交出去.
        return str(first["msg"]).removeprefix("Value error, ")
    template = _REASONS.get(first["type"])
    if template is None:
        # 认不出的错误类型宁可露出原文, 也好过说一句正确但没信息的废话.
        return f"{where}: {first['msg']}"
    reason = template.format(**first.get("ctx", {}))
    return f"{where} {reason}，收到: {first.get('input')!r}"


def _menu_kind(annotation: object) -> type:
    """菜单字段的原生类型: `int | None` 这样的标注要剥到 int.

    只认 int / float / str; 将来给某个字段换了别的类型, 下面那句 raise 会在 import
    这个模块时就炸掉, 而不是让菜单静默地把它当字符串收.
    """
    text = str(annotation)
    if "int" in text:
        return int
    if "float" in text:
        return float
    if "str" in text:
        return str
    raise TypeError(f"菜单字段只支持 int / float / str, 收到: {annotation!r}")


def _menu_fields(model: type[BaseModel]) -> tuple[StandardField, ...]:
    """一个模型里带了菜单标签的字段, 顺序即声明顺序."""
    return tuple(
        StandardField(
            name=name,
            label=str((info.json_schema_extra or {})[_MENU_LABEL]),
            kind=_menu_kind(info.annotation),
        )
        for name, info in model.model_fields.items()
        if isinstance(info.json_schema_extra, dict)
        and _MENU_LABEL in info.json_schema_extra
    )


def _effort_name(value: object) -> ThinkingEffortName:
    """一个 thinking 强度名. 配置里写的是字符串, 领域里是值对象."""
    if not isinstance(value, str):
        raise ValueError("thinking effort 必须是字符串")
    return ThinkingEffortName(value)


# 配置文件里 thinking 强度是字符串, 而领域词汇是 ThinkingEffortName. 两个方向的转换都
# 写在类型上, 于是每个用到这个类型的字段自动带上它们.
#
# 回写那一半 (PlainSerializer) 是必须的: ThinkingEffortName 是个普通 dataclass,
# Pydantic 默认会把它序列化成 `{"value": "low"}`, 而配置文件里它一直是 `"low"`.
EffortName = Annotated[
    ThinkingEffortName,
    BeforeValidator(_effort_name),
    PlainSerializer(lambda name: name.value, return_type=str),
]

# 菜单元数据的键. 带上它的字段会出现在 /config 的模型编辑菜单里, 值就是那一行的标签.
_MENU_LABEL = "forge_menu_label"


def _menu(label: str) -> dict[str, JsonValue]:
    """Field 的 json_schema_extra 参数.

    值类型标成 JsonValue 而不是 str: Pydantic 那个参数的标注是不变 (invariant) 的
    dict, 传一个更窄的值类型进去过不了类型检查.
    """
    return {_MENU_LABEL: label}


@dataclass(frozen=True)
class StandardField:
    """一个标准化模型字段的 UI / 解析元数据。"""

    name: str
    label: str
    kind: type  # int | float | str
    choices: tuple[str, ...] = ()


class ModelParams(BaseModel):
    """一个模型的参数: 能力/计费元数据 + 标准超参 + 厂商自定义 (extra).

    这里原先是四份互相重复的表: 一个 `_KNOWN_FIELDS` 集合说"哪些键我们认识", 一个
    `STANDARD_FIELDS` 元组说"菜单里能编辑哪些, 标签是什么, 类型是什么", 一个由它派生的
    `_FIELD_KIND`, 再加上 dataclass 自己的字段声明. 加一个字段要改四处, 漏掉任何一处都
    不会报错 —— 只会让新字段在菜单里编不了, 或者被静默折进 extra.

    现在只有下面这一份声明 (ADR-0040 决策 4.2). 认识哪些键, 每个键什么类型, 取值范围,
    菜单里叫什么, 全从它派生.

    `strict=True` 是刻意的, 它复刻换掉的手写校验:

        - 配置里写 `"128000"` 是笔误, 不是一种写法, 不做字符串到整数的转换;
        - 写 `true` 同理 —— Python 里 True 是 1, 但配置里它是笔误.

    浮点字段仍收整数 (`cost_per_1k_input = 1` 得到 1.0): 严格模式对 float 收 int 是
    Pydantic 的既定行为, 也正是换掉的 `_as_nonneg_float` 的行为.

    `extra="forbid"`: 认不出的键在下面的 `_fold_unknown_keys` 里已经被折进 `extra` 了,
    走到校验这一步还剩下未知键只可能是程序错误.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    # 声明顺序就是 /config 模型编辑菜单里的行顺序.
    context_window: int | None = Field(
        default=None, gt=0, json_schema_extra=_menu("上下文窗口")
    )
    max_tokens: int | None = Field(
        default=None, gt=0, json_schema_extra=_menu("最大输出 tokens")
    )
    temperature: float | None = Field(
        default=None, ge=0.0, le=2.0, json_schema_extra=_menu("温度")
    )
    top_p: float | None = Field(
        default=None, ge=0.0, le=1.0, json_schema_extra=_menu("top_p")
    )
    # cached / reasoning 单价为 ADR-0012 §7 新增; 菜单随本表自动获得两行.
    cost_per_1k_input: float = Field(
        default=0.0, ge=0.0, json_schema_extra=_menu("输入价格/1k")
    )
    cost_per_1k_output: float = Field(
        default=0.0, ge=0.0, json_schema_extra=_menu("输出价格/1k")
    )
    cost_per_1k_cached_input: float = Field(
        default=0.0, ge=0.0, json_schema_extra=_menu("缓存输入价格/1k")
    )
    cost_per_1k_reasoning: float = Field(
        default=0.0, ge=0.0, json_schema_extra=_menu("思考输出价格/1k")
    )

    # 模型 thinking 强度声明. 不进菜单: 它是模型能力, 不是用户偏好.
    #
    # 这几个字段单独放宽 strict: JSON 里没有元组, 强度列表读上来是 list; 枚举写的是
    # 它的字符串取值而不是枚举成员. 放宽的只是"长什么样算这个类型", 不是取值范围 ——
    # 未知的 thinking_mode 仍然被拒, 强度名的形状仍由 ThinkingEffortName 自己把关.
    thinking_efforts: tuple[EffortName, ...] | None = Field(default=None, strict=False)
    thinking_default_effort: EffortName | None = None

    # 用户对当前模型的 thinking 设置.
    thinking_mode: ThinkingMode | None = Field(default=None, strict=False)
    thinking_effort: EffortName | None = None

    extra: Mapping[str, object] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _fold_unknown_keys(cls, data: object) -> object:
        """认不出的键归进 extra, 供具体供应商 adapter 使用.

        显式写的 `extra` 覆盖折进来的同名键: 用户明确写在 extra 里的那份更晚, 也更明确.
        """
        if not isinstance(data, Mapping):
            return data
        declared = set(cls.model_fields)
        explicit = data.get("extra", {})
        if not isinstance(explicit, Mapping):
            raise ValueError("extra 必须是 JSON 对象（键值表）")
        folded = {key: value for key, value in data.items() if key not in declared}
        folded.update(explicit)
        cleaned = {key: value for key, value in data.items() if key in declared}
        cleaned["extra"] = folded
        return cleaned

    @model_validator(mode="after")
    def _thinking_declaration_is_consistent(self) -> ModelParams:
        """默认强度必须在候选强度里 —— 判据住在 domain, 这里只负责问它一次."""
        capabilities = ModelThinkingCapabilities(
            efforts=self.thinking_efforts or (),
            default_effort=self.thinking_default_effort,
        )
        capabilities.validate(
            ModelThinkingSettings(
                mode=self.thinking_mode or ThinkingMode.OFF,
                effort=self.thinking_effort,
            )
        )
        return self

    def to_fields(self) -> dict[str, object]:
        """转回可写入 JSON 的字段 dict, 只包含已设置的项目.

        默认值不回写: 把它们固化进配置文件, 以后改默认值就影响不到已有配置了.
        `exclude_defaults` 表达的正是这条, 所以这里不再逐字段 if.
        """
        return self.model_dump(mode="json", exclude_defaults=True)

    @classmethod
    def parse(cls, raw: Mapping[str, object]) -> ModelParams:
        """从模型配置构造参数对象；非法值抛 ConfigValidationError。"""
        try:
            return cls.model_validate(raw)
        except ValidationError as exc:
            raise ConfigValidationError(explain_validation_error(exc)) from exc


# 模型菜单里能编辑的字段.
STANDARD_FIELDS: tuple[StandardField, ...] = _menu_fields(ModelParams)


def coerce_field(name: str, raw: str) -> int | float | str:
    """把菜单文本输入转成字段的原生类型；不能解析时抛 ConfigValidationError。

    只做类型转换，范围校验仍由 ModelParams.parse 统一负责。
    """
    field = next((item for item in STANDARD_FIELDS if item.name == name), None)
    if field is None:
        raise ConfigValidationError(f"未知模型字段: {name}")
    text = raw.strip()
    if field.kind is str:
        if text not in field.choices:
            allowed = " / ".join(field.choices)
            raise ConfigValidationError(f"{name} 只能是 [{allowed}]，收到: {raw!r}")
        return text
    try:
        return int(text) if field.kind is int else float(text)
    except ValueError:
        expected = "整数" if field.kind is int else "数字"
        raise ConfigValidationError(f"{name} 必须是{expected}，收到: {raw!r}") from None


def parse_extra(raw: str) -> dict[str, object]:
    """把一行 JSON 文本解析成 extra 对象；非法或非对象时抛 ConfigValidationError。"""
    text = raw.strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigValidationError(f"extra 不是合法 JSON：{exc}") from None
    if not isinstance(value, dict):
        raise ConfigValidationError("extra 必须是 JSON 对象（{...}）")
    return value


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    id: str
    params: ModelParams


class ProviderConfig(BaseModel):
    """一家供应商的配置段.

    字段名和类型原先写在三处: 这个类声明一次, `_parse_provider` 构造时带默认值再读一次,
    菜单编辑那条路上还有两个按类型分组的集合 (`_PROVIDER_STR_FIELDS` /
    `_PROVIDER_INT_FIELDS`) 分第三次. 现在只有这一份 (ADR-0040 决策 4.2).

    `strict=True` 的理由与 ModelParams 相同: 配置里写 `timeout = "30"` 是笔误.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    id: str
    name: str = Field(json_schema_extra=_menu("展示名"))
    api_base: str = Field(json_schema_extra=_menu("API 地址"))
    api_key_env: str | None = Field(
        default=None, json_schema_extra=_menu("API Key 环境变量")
    )
    timeout: int = Field(default=60, ge=0, json_schema_extra=_menu("超时(秒)"))
    max_retries: int = Field(default=2, ge=0, json_schema_extra=_menu("重试次数"))
    models: tuple[ModelSpec, ...] = ()
    # 可选 key pool (ADR-0011 §7 / §16): 多个 credential reference (环境变量名, 如
    # "DEEPSEEK_API_KEY"). 缺省时由 settings 层从 api_key_env 派生单条引用.
    # 只支持手写配置, 不进 /config 菜单编辑 (所以没有菜单标签); 明文 key 永不入配置.
    credential_refs: tuple[str, ...] = ()

    @field_validator("credential_refs", mode="before")
    @classmethod
    def _trim_credential_refs(cls, value: object) -> object:
        """凭证引用是环境变量名.

        空白项一定是手写配置时的笔误, 不能静默留下来 —— 它会变成一次"读不到 key"的
        运行时失败, 而错误现场在这里.
        """
        if value is None:
            return ()
        if not isinstance(value, list | tuple) or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            raise ValueError(f"credential_refs 必须是非空字符串数组，收到: {value!r}")
        return tuple(item.strip() for item in value)

    def model(self, model_id: str) -> ModelSpec | None:
        return next((m for m in self.models if m.id == model_id), None)


# 供应商菜单里能编辑的字段.
PROVIDER_FIELDS: tuple[StandardField, ...] = _menu_fields(ProviderConfig)


def coerce_provider_field(name: str, raw: str) -> int | str:
    """把菜单文本输入转成供应商字段的原生类型.

    和 `coerce_field` 分开是因为空串的含义相反: 模型字段留空表示"清除这个可选项",
    而供应商没有 name 或 api_base 就没法发请求, 留空只能是拒绝.
    """
    field = next((item for item in PROVIDER_FIELDS if item.name == name), None)
    if field is None:
        raise ConfigValidationError(f"未知供应商字段: {name}")
    text = raw.strip()
    if field.kind is str:
        if not text:
            raise ConfigValidationError(f"{name} 不能为空")
        return text
    try:
        number = int(text)
    except ValueError:
        raise ConfigValidationError(f"{name} 必须是整数，收到: {raw!r}") from None
    if number < 0:
        raise ConfigValidationError(f"{name} 不能为负")
    return number


# ---- 网关运行时配置段（ADR-0012）----
# 以下值对象对应 llm.json  的 llm.cache / llm.circuit_breaker / llm.retry，
# 由 LlmConfigService 解析、wiring 消费装配。所有能力默认关闭或回落现行为；
# 网关自身不解析 TOML（ADR-0011 §4 边界不变）。已移除的配置段：[llm.rate_limit]
# （客户端主动限流，见 governance.py）、[llm.credentials]（凭证只支持环境变量，
# 无 dotenv 开关，见 infrastructure/llm/credentials.py）。


# 响应缓存 origins 白名单的默认值（ADR-0012 §3 配置示例）。
_DEFAULT_CACHE_ORIGINS = (
    RequestOrigin.TITLE,
    RequestOrigin.SUMMARY,
    RequestOrigin.STRUCTURED_CLASSIFICATION,
)


@dataclass(frozen=True)
class CacheSettings:
    """[llm.cache]（ADR-0012 §3）：响应缓存开关与参数。默认关闭。"""

    enabled: bool = False
    ttl_seconds: float | None = 600.0
    max_entries: int = 256
    origins: tuple[RequestOrigin, ...] = _DEFAULT_CACHE_ORIGINS


@dataclass(frozen=True)
class CircuitBreakerSettings:
    """[llm.circuit_breaker]（ADR-0012 §8）：健康熔断。默认关闭。"""

    enabled: bool = False
    failure_threshold: int = 5
    cooldown_seconds: float = 30.0


@dataclass(frozen=True)
class RetrySettings:
    """[llm.retry]（ADR-0012 §2）：429 短等重试阈值（秒）。"""

    wait_threshold_seconds: float = 5.0


@dataclass(frozen=True)
class LlmConfig:
    """整份有效 LLM 配置（只读视图）。"""

    providers: tuple[ProviderConfig, ...]

    def provider(self, provider_id: str) -> ProviderConfig | None:
        return next((p for p in self.providers if p.id == provider_id), None)

    def model(self, provider_id: str, model_id: str) -> ModelSpec | None:
        provider = self.provider(provider_id)
        return provider.model(model_id) if provider else None

    def all_models(self) -> tuple[ModelSpec, ...]:
        return tuple(m for p in self.providers for m in p.models)
