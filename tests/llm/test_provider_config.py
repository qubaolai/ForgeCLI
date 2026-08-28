"""供应商配置段的解析与编辑约定 (ADR-0040 决策 4.2).

`ProviderConfig` 的字段名和类型原先写在三处: dataclass 声明一次, `_parse_provider`
构造时带默认值再读一次, 菜单编辑那条路上还有两个按类型分组的集合
(`_PROVIDER_STR_FIELDS` / `_PROVIDER_INT_FIELDS`) 分第三次. 加一个字段要改三处.

和 ModelParams 那次一样: 先把现在的行为写成用例, 再收敛成一份声明.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from forgecli.application.llm.config.llm_config import PROVIDER_FIELDS
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.llm.config.llm_config_store import LlmConfigStore
from forgecli.application.llm.errors import ConfigValidationError, UnknownProvider
from forgecli.domain.model.thinking import ThinkingMode


class _MemoryStore(LlmConfigStore):
    """内存里的 llm.json. 写入原样记下来, 供用例断言写出去的是什么."""

    def __init__(self, section: dict[str, object] | None = None) -> None:
        self.section: dict[str, object] = section or {}
        self.provider_writes: list[tuple[str, str, object]] = []
        self.model_writes: list[tuple[str, str, Mapping[str, object]]] = []
        self.model_defaults: list[Mapping[str, object]] = []

    def load(self) -> dict[str, object]:
        return self.section

    def upsert_model(
        self,
        provider_id: str,
        model_id: str,
        fields: Mapping[str, object],
        *,
        provider_defaults: Mapping[str, object],
    ) -> None:
        self.model_writes.append((provider_id, model_id, dict(fields)))
        self.model_defaults.append(dict(provider_defaults))

    def remove_model(self, provider_id: str, model_id: str) -> None:
        return None

    def upsert_provider_field(
        self,
        provider_id: str,
        field: str,
        value: object,
        *,
        provider_defaults: Mapping[str, object],
    ) -> None:
        self.provider_writes.append((provider_id, field, value))

    def upsert_runtime_field(self, section: str, field: str, value: object) -> None:
        return None


def _service(providers: dict[str, object]) -> LlmConfigService:
    return LlmConfigService(_MemoryStore({"providers": providers}))


# ---- 默认值来自注册表 ----


def test_an_empty_provider_section_falls_back_to_registry_defaults() -> None:
    provider = _service({"deepseek": {}}).providers()[0]
    assert provider.id == "deepseek"
    assert provider.name == "DeepSeek"
    assert provider.api_base == "https://api.deepseek.com/chat/completions"
    assert provider.api_key_env == "DEEPSEEK_API_KEY"


def test_configured_values_win_over_registry_defaults() -> None:
    provider = _service(
        {
            "deepseek": {
                "name": "自建",
                "api_base": "https://example.invalid/v1/chat/completions",
                "api_key_env": "MY_KEY",
            }
        }
    ).providers()[0]
    assert provider.name == "自建"
    assert provider.api_base == "https://example.invalid/v1/chat/completions"
    assert provider.api_key_env == "MY_KEY"


def test_timeout_and_retries_have_defaults() -> None:
    provider = _service({"deepseek": {}}).providers()[0]
    assert provider.timeout == 60
    assert provider.max_retries == 2


def test_timeout_and_retries_can_be_configured() -> None:
    provider = _service({"deepseek": {"timeout": 5, "max_retries": 0}}).providers()[0]
    assert provider.timeout == 5
    assert provider.max_retries == 0


@pytest.mark.parametrize("field", ["timeout", "max_retries"])
@pytest.mark.parametrize("bad", ["30", 1.5, True])
def test_integer_fields_reject_wrong_types(field: str, bad: object) -> None:
    with pytest.raises(ConfigValidationError):
        _service({"deepseek": {field: bad}}).providers()


# ---- 模型 ----


def test_models_are_parsed_under_their_provider() -> None:
    provider = _service(
        {"deepseek": {"models": {"deepseek-chat": {"context_window": 64000}}}}
    ).providers()[0]
    assert [model.id for model in provider.models] == ["deepseek-chat"]
    assert provider.models[0].provider == "deepseek"
    assert provider.models[0].params.context_window == 64000
    assert provider.model("deepseek-chat") is not None
    assert provider.model("nope") is None


def test_thinking_edits_copy_the_pydantic_provider_without_a_type_error() -> None:
    """ProviderConfig 迁移到 Pydantic 后，候选配置不能再用 dataclasses.replace。"""
    store = _MemoryStore({"providers": {"deepseek": {"models": {"deepseek-chat": {}}}}})
    service = LlmConfigService(store)

    assert service.update_model_thinking(
        "deepseek", "deepseek-chat", mode=ThinkingMode.OFF
    )
    assert store.model_writes[-1][2]["thinking_mode"] == "off"


# ---- credential_refs ----


def test_credential_refs_default_to_empty() -> None:
    assert _service({"deepseek": {}}).providers()[0].credential_refs == ()


def test_credential_refs_are_trimmed() -> None:
    provider = _service(
        {"deepseek": {"credential_refs": [" A_KEY ", "B_KEY"]}}
    ).providers()[0]
    assert provider.credential_refs == ("A_KEY", "B_KEY")


@pytest.mark.parametrize("bad", ["A_KEY", [""], [1], ["  "]])
def test_credential_refs_reject_anything_but_non_empty_strings(bad: object) -> None:
    with pytest.raises(ConfigValidationError):
        _service({"deepseek": {"credential_refs": bad}}).providers()


# ---- 菜单编辑 ----


def _edit(field: str, raw: str) -> tuple[str, object] | None:
    store = _MemoryStore({"providers": {"deepseek": {}}})
    LlmConfigService(store).set_provider_field("deepseek", field, raw)
    if not store.provider_writes:
        return None
    _, written_field, value = store.provider_writes[-1]
    return (written_field, value)


def test_editing_a_string_field_writes_the_trimmed_text() -> None:
    assert _edit("name", "  自建  ") == ("name", "自建")


def test_editing_an_integer_field_writes_a_number() -> None:
    assert _edit("timeout", " 90 ") == ("timeout", 90)


def test_a_string_field_cannot_be_cleared_to_empty() -> None:
    """供应商字段和模型字段在这里不同: 模型字段留空表示"清除这个可选项",
    而供应商没有 name 或 api_base 就没法发请求."""
    with pytest.raises(ConfigValidationError):
        _edit("name", "   ")


def test_an_integer_field_rejects_unparsable_text() -> None:
    with pytest.raises(ConfigValidationError):
        _edit("timeout", "很久")


def test_an_integer_field_rejects_negative_values() -> None:
    with pytest.raises(ConfigValidationError):
        _edit("timeout", "-1")


def test_an_unknown_provider_field_is_refused() -> None:
    with pytest.raises(ConfigValidationError):
        _edit("models", "x")


def test_editing_an_unknown_provider_is_refused() -> None:
    store = _MemoryStore({"providers": {}})
    with pytest.raises(UnknownProvider):
        LlmConfigService(store).set_provider_field("nope", "name", "x")


# ---- 菜单字段表与解析来自同一份声明 ----


def test_every_editable_provider_field_is_an_attribute() -> None:
    """菜单按字段名 getattr 取值, 所以表里的每个名字都必须真的是一个属性."""
    provider = _service({"deepseek": {}}).providers()[0]
    for spec in PROVIDER_FIELDS:
        assert hasattr(provider, spec.name), spec.name


def test_every_editable_provider_field_can_be_written_back() -> None:
    samples: dict[type, str] = {str: "x", int: "7"}
    for spec in PROVIDER_FIELDS:
        written = _edit(spec.name, samples[spec.kind])
        assert written is not None and written[0] == spec.name


def test_the_editable_set_matches_what_the_menu_used_to_hardcode() -> None:
    """菜单原先自带一份 (标签, 字段名) 元组. 收敛之后这几行是那份表的替代物 ——
    少一个字段就是菜单里少一行, 而那不会有任何测试变红."""
    assert [spec.name for spec in PROVIDER_FIELDS] == [
        "name",
        "api_base",
        "api_key_env",
        "timeout",
        "max_retries",
    ]
    assert [spec.label for spec in PROVIDER_FIELDS] == [
        "展示名",
        "API 地址",
        "API Key 环境变量",
        "超时(秒)",
        "重试次数",
    ]


# ---- 同一家供应商在文件里有两个段落 ----
#
# 注册表的 key 曾经是 "GLM", 早期版本的菜单照着它写出了 providers.GLM. key 改回 "glm"
# 之后, 新的写入落在 providers.glm 上 —— 同一家供应商在文件里有了两份。
#
# 这组用例钉的是那次事故的两半: 读的时候要合并, 写的时候不能再产生第二份。


LEGACY_SECTION = {
    "GLM": {
        "name": "GLM",
        "api_base": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "api_key_env": "GLM_API_KEY",
        "models": {"glm-4": {"context_window": 128000}},
    }
}


def test_a_legacy_uppercase_section_is_still_readable() -> None:
    providers = _service(dict(LEGACY_SECTION)).providers()
    assert [provider.id for provider in providers] == ["glm"]
    assert [model.id for model in providers[0].models] == ["glm-4"]


def test_two_sections_for_one_provider_are_merged() -> None:
    """不合并的话 provider() 只命中先出现的那一份.

    另一份里的模型就成了"存进去了但找不到".
    """
    providers = _service(
        {
            **LEGACY_SECTION,
            "glm": {"name": "GLM", "models": {"glm-4-plus": {}}},
        }
    ).providers()

    assert [provider.id for provider in providers] == ["glm"]
    assert sorted(model.id for model in providers[0].models) == ["glm-4", "glm-4-plus"]


def test_the_later_section_wins_for_scalar_fields() -> None:
    """一个键只有在有人往那个段落写过的时候才会出现, 所以后写的就是最近一次编辑."""
    provider = _service(
        {
            **LEGACY_SECTION,
            "glm": {"api_base": "https://proxy.invalid/v4/chat/completions"},
        }
    ).providers()[0]

    assert provider.api_base == "https://proxy.invalid/v4/chat/completions"
    # 没被后写覆盖的键保留前一段的取值.
    assert provider.api_key_env == "GLM_API_KEY"


def test_writes_never_create_a_second_section() -> None:
    """回归: 归一只做在读那一侧, 写入就会新建一个 `glm` 段落."""
    store = _MemoryStore({"providers": dict(LEGACY_SECTION)})
    LlmConfigService(store).add_model("GLM", "glm-4-plus", {})

    assert [written[0] for written in store.model_writes] == ["glm"]


def test_every_write_path_normalises_the_provider_id() -> None:
    """漏掉任何一条都会重新长出第二个段落, 而它不会报错, 只会让查找静默落空."""
    store = _MemoryStore({"providers": dict(LEGACY_SECTION)})
    service = LlmConfigService(store)

    service.add_model("GLM", "m1", {})
    service.set_provider_field("GLM", "timeout", "30")
    service.set_model_field("GLM", "glm-4", "context_window", "64000")
    service.set_model_extra("GLM", "glm-4", '{"seed": 1}')

    assert {written[0] for written in store.model_writes} == {"glm"}
    assert {written[0] for written in store.provider_writes} == {"glm"}


def test_adding_a_model_keeps_a_customised_endpoint() -> None:
    """新建段落时用当前有效值播种, 而不是注册表默认值.

    否则用户改过的 api_base 会因为"又加了一个模型"被默认值盖回去。
    """
    store = _MemoryStore(
        {
            "providers": {
                "deepseek": {"api_base": "https://proxy.invalid/v1/chat/completions"}
            }
        }
    )
    LlmConfigService(store).add_model("deepseek", "deepseek-chat", {})

    assert store.model_defaults[-1]["api_base"] == (
        "https://proxy.invalid/v1/chat/completions"
    )


def test_an_unknown_provider_section_is_ignored() -> None:
    """没有 adapter 能驱动它, 留着只会让下游每一处都要再判一次."""
    providers = _service(
        {**LEGACY_SECTION, "anthropic": {"models": {"x": {}}}}
    ).providers()
    assert [provider.id for provider in providers] == ["glm"]
