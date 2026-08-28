"""模型参数的解析与校验约定 (ADR-0040 决策 4.2 换成 Pydantic 之前没有任何用例).

这一层是配置文件进入系统的关口: 用户手改 llm.json, 或者在 /config 菜单里敲一个值, 都从
这里过。它错了不会崩, 只会让一个模型带着离谱的参数去调用 —— temperature 5.0, 或者
context_window 是字符串 "128000".

所以先把现在的行为写成用例, 再换实现。这些用例问的是"给这份配置, 解出什么", 不问用什么
库解的。
"""

from __future__ import annotations

import pytest

from forgecli.application.llm.config.llm_config import (
    STANDARD_FIELDS,
    ModelParams,
    coerce_field,
    parse_extra,
)
from forgecli.application.llm.errors import ConfigValidationError
from forgecli.domain.model.thinking import ThinkingEffortName, ThinkingMode

# ---- 空配置与默认值 ----


def test_empty_config_gives_all_defaults() -> None:
    params = ModelParams.parse({})
    assert params.context_window is None
    assert params.max_tokens is None
    assert params.temperature is None
    assert params.top_p is None
    assert params.cost_per_1k_input == 0.0
    assert params.cost_per_1k_output == 0.0
    assert params.cost_per_1k_cached_input == 0.0
    assert params.cost_per_1k_reasoning == 0.0
    assert params.thinking_mode is None
    assert params.thinking_effort is None
    assert params.thinking_efforts is None
    assert params.thinking_default_effort is None
    assert dict(params.extra) == {}


# ---- 正整数字段 ----


@pytest.mark.parametrize("name", ["context_window", "max_tokens"])
def test_positive_int_fields_accept_positive_integers(name: str) -> None:
    assert getattr(ModelParams.parse({name: 128000}), name) == 128000


@pytest.mark.parametrize("name", ["context_window", "max_tokens"])
@pytest.mark.parametrize("bad", [0, -1])
def test_positive_int_fields_reject_non_positive(name: str, bad: int) -> None:
    with pytest.raises(ConfigValidationError):
        ModelParams.parse({name: bad})


@pytest.mark.parametrize("name", ["context_window", "max_tokens"])
@pytest.mark.parametrize("bad", ["128000", 1.5, None if False else [1]])
def test_positive_int_fields_reject_wrong_types(name: str, bad: object) -> None:
    """字符串数字也不行: 配置文件写 "128000" 是个错误, 不是一种写法."""
    with pytest.raises(ConfigValidationError):
        ModelParams.parse({name: bad})


@pytest.mark.parametrize("name", ["context_window", "max_tokens"])
def test_positive_int_fields_reject_booleans(name: str) -> None:
    """True 在 Python 里是 1, 但配置里写 true 是笔误, 不该被当成 1 收下."""
    with pytest.raises(ConfigValidationError):
        ModelParams.parse({name: True})


# ---- 非负浮点 (单价) ----


COST_FIELDS = [
    "cost_per_1k_input",
    "cost_per_1k_output",
    "cost_per_1k_cached_input",
    "cost_per_1k_reasoning",
]


@pytest.mark.parametrize("name", COST_FIELDS)
def test_cost_fields_accept_zero_and_positive(name: str) -> None:
    assert getattr(ModelParams.parse({name: 0}), name) == 0.0
    assert getattr(ModelParams.parse({name: 0.014}), name) == pytest.approx(0.014)


@pytest.mark.parametrize("name", COST_FIELDS)
def test_cost_fields_reject_negative(name: str) -> None:
    with pytest.raises(ConfigValidationError):
        ModelParams.parse({name: -0.001})


@pytest.mark.parametrize("name", COST_FIELDS)
def test_cost_fields_reject_booleans_and_strings(name: str) -> None:
    for bad in (True, "0.01"):
        with pytest.raises(ConfigValidationError):
            ModelParams.parse({name: bad})


def test_integer_cost_becomes_float() -> None:
    assert isinstance(
        ModelParams.parse({"cost_per_1k_input": 1}).cost_per_1k_input, float
    )


# ---- 有范围的浮点 ----


@pytest.mark.parametrize(
    ("name", "low", "high"),
    [("temperature", 0.0, 2.0), ("top_p", 0.0, 1.0)],
)
def test_ranged_fields_accept_both_ends(name: str, low: float, high: float) -> None:
    assert getattr(ModelParams.parse({name: low}), name) == low
    assert getattr(ModelParams.parse({name: high}), name) == high


@pytest.mark.parametrize(
    ("name", "bad"),
    [
        ("temperature", -0.1),
        ("temperature", 2.1),
        ("top_p", -0.1),
        ("top_p", 1.1),
    ],
)
def test_ranged_fields_reject_out_of_range(name: str, bad: float) -> None:
    with pytest.raises(ConfigValidationError):
        ModelParams.parse({name: bad})


# ---- thinking ----


def test_thinking_mode_accepts_declared_values() -> None:
    parsed = ModelParams.parse({"thinking_mode": ThinkingMode.OFF.value})
    assert parsed.thinking_mode is ThinkingMode.OFF


def test_thinking_mode_rejects_unknown_value() -> None:
    with pytest.raises(ConfigValidationError):
        ModelParams.parse({"thinking_mode": "sometimes"})


def test_thinking_mode_rejects_non_string() -> None:
    with pytest.raises(ConfigValidationError):
        ModelParams.parse({"thinking_mode": 1})


def test_thinking_efforts_parse_into_value_objects() -> None:
    parsed = ModelParams.parse({"thinking_efforts": ["low", "high"]})
    assert parsed.thinking_efforts == (
        ThinkingEffortName("low"),
        ThinkingEffortName("high"),
    )


def test_thinking_efforts_rejects_non_list() -> None:
    with pytest.raises(ConfigValidationError):
        ModelParams.parse({"thinking_efforts": "high"})


def test_thinking_efforts_rejects_malformed_member() -> None:
    """强度名的形状由 domain 定 (小写标识符); 这里只负责把违规翻成配置错误."""
    with pytest.raises(ConfigValidationError):
        ModelParams.parse({"thinking_efforts": ["NOT VALID"]})


def test_thinking_efforts_rejects_non_string_member() -> None:
    with pytest.raises(ConfigValidationError):
        ModelParams.parse({"thinking_efforts": [1]})


def test_declared_default_effort_must_be_among_declared_efforts() -> None:
    """能力声明自己要自洽 —— 默认强度不在候选里, 那个默认值指不到任何东西."""
    with pytest.raises(ConfigValidationError):
        ModelParams.parse(
            {"thinking_efforts": ["low"], "thinking_default_effort": "high"}
        )


# ---- extra: 认不出的键去哪 ----


def test_unknown_keys_are_folded_into_extra() -> None:
    """认不出的键不能丢: 它多半是某家供应商的自定义参数, adapter 还要用."""
    parsed = ModelParams.parse({"reasoning_effort": "high", "seed": 7})
    assert dict(parsed.extra) == {"reasoning_effort": "high", "seed": 7}


def test_explicit_extra_wins_over_folded_keys() -> None:
    parsed = ModelParams.parse({"seed": 1, "extra": {"seed": 2}})
    assert dict(parsed.extra) == {"seed": 2}


def test_extra_must_be_a_mapping() -> None:
    with pytest.raises(ConfigValidationError):
        ModelParams.parse({"extra": [1, 2]})


def test_known_field_names_are_never_folded_into_extra() -> None:
    parsed = ModelParams.parse({"context_window": 8, "temperature": 0.5})
    assert dict(parsed.extra) == {}


# ---- 回写 ----


def test_to_fields_only_writes_what_was_set() -> None:
    """回写不能把默认值固化进配置文件: 那样以后改默认值就影响不到已有配置."""
    assert ModelParams.parse({}).to_fields() == {}


def test_to_fields_round_trips_a_full_config() -> None:
    raw = {
        "context_window": 128000,
        "max_tokens": 4096,
        "temperature": 0.7,
        "top_p": 0.9,
        "cost_per_1k_input": 0.001,
        "cost_per_1k_output": 0.002,
        "cost_per_1k_cached_input": 0.0005,
        "cost_per_1k_reasoning": 0.003,
        "extra": {"seed": 7},
    }
    assert ModelParams.parse(raw).to_fields() == raw


def test_to_fields_writes_plain_strings_not_value_objects() -> None:
    """写进 JSON 的必须是字符串; 值对象和枚举都序列化不了."""
    written = ModelParams.parse(
        {
            "thinking_mode": ThinkingMode.OFF.value,
            "thinking_efforts": ["low", "high"],
            "thinking_default_effort": "low",
        }
    ).to_fields()
    assert written["thinking_mode"] == ThinkingMode.OFF.value
    assert written["thinking_efforts"] == ["low", "high"]
    assert written["thinking_default_effort"] == "low"


def test_zero_costs_are_not_written_back() -> None:
    assert (
        "cost_per_1k_input"
        not in ModelParams.parse({"cost_per_1k_input": 0}).to_fields()
    )


def test_extra_json_is_a_single_line() -> None:
    assert ModelParams.parse({}).extra_json() == ""
    assert ModelParams.parse({"extra": {"a": 1}}).extra_json() == '{"a": 1}'


# ---- 菜单文本输入 ----


def test_coerce_field_parses_by_declared_kind() -> None:
    assert coerce_field("context_window", " 128000 ") == 128000
    assert coerce_field("temperature", "0.7") == pytest.approx(0.7)


def test_coerce_field_rejects_unparsable_text() -> None:
    with pytest.raises(ConfigValidationError):
        coerce_field("context_window", "很大")


def test_coerce_field_rejects_unknown_field() -> None:
    with pytest.raises(ConfigValidationError):
        coerce_field("nope", "1")


def test_parse_extra_accepts_empty_and_objects() -> None:
    assert parse_extra("  ") == {}
    assert parse_extra('{"a": 1}') == {"a": 1}


def test_parse_extra_rejects_non_object_json() -> None:
    for bad in ("[1]", "1", '"x"'):
        with pytest.raises(ConfigValidationError):
            parse_extra(bad)


def test_parse_extra_rejects_malformed_json() -> None:
    with pytest.raises(ConfigValidationError):
        parse_extra("{")


# ---- 菜单字段表与解析必须来自同一份事实 ----


def test_every_editable_field_is_parseable() -> None:
    """菜单里能编辑的字段, 解析器必须认识它 —— 否则用户能存下一个存不进去的值."""
    for spec in STANDARD_FIELDS:
        sample = 1 if spec.kind is int else 0.5
        parsed = ModelParams.parse({spec.name: sample})
        assert getattr(parsed, spec.name) == sample


def test_every_editable_field_survives_a_round_trip() -> None:
    for spec in STANDARD_FIELDS:
        sample = 1 if spec.kind is int else 0.5
        assert ModelParams.parse({spec.name: sample}).to_fields()[spec.name] == sample
