"""价格口径补全（ADR-0012 §7）：配置字段、目录合并与 CostEstimator。"""

from __future__ import annotations

from forgecli.application.llm.catalog_builder import build_catalog
from forgecli.application.llm.config.llm_config import (
    STANDARD_FIELDS,
    LlmConfig,
    ModelParams,
    ModelSpec,
    ProviderConfig,
)
from forgecli.application.llm.gateway import ModelUsage
from forgecli.application.llm.metering import CostEstimator, UnitPrices
from forgecli.application.llm.model_ref import ModelRef


def _config_with(params: ModelParams) -> LlmConfig:
    spec = ModelSpec(provider="deepseek", id="deepseek-chat", params=params)
    provider = ProviderConfig(
        id="deepseek",
        name="DeepSeek",
        api_base="https://api.deepseek.com",
        api_key_env="DEEPSEEK_API_KEY",
        timeout=60,
        max_retries=2,
        models=(spec,),
    )
    return LlmConfig(providers=(provider,))


def test_standard_fields_include_cached_and_reasoning_prices() -> None:
    names = [field.name for field in STANDARD_FIELDS]
    assert "cost_per_1k_cached_input" in names
    assert "cost_per_1k_reasoning" in names


def test_model_params_parse_and_roundtrip_new_price_fields() -> None:
    params = ModelParams.parse(
        {
            "cost_per_1k_cached_input": 0.05,
            "cost_per_1k_reasoning": 0.8,
        }
    )
    assert params.cost_per_1k_cached_input == 0.05
    assert params.cost_per_1k_reasoning == 0.8
    fields = params.to_fields()
    assert fields["cost_per_1k_cached_input"] == 0.05
    assert fields["cost_per_1k_reasoning"] == 0.8
    assert "extra" not in fields  # 新字段是标准字段，不落入 extra


def test_catalog_builder_merges_new_prices_from_config() -> None:
    catalog = build_catalog(
        _config_with(
            ModelParams.parse(
                {
                    "cost_per_1k_input": 0.2,
                    "cost_per_1k_output": 0.4,
                    "cost_per_1k_cached_input": 0.05,
                    "cost_per_1k_reasoning": 0.8,
                }
            )
        )
    )
    entry = catalog.get(ModelRef(provider="deepseek", model="deepseek-chat"))
    assert entry.cached_input_price_per_1k == 0.05
    assert entry.reasoning_price_per_1k == 0.8


def test_catalog_builder_unset_prices_stay_unknown() -> None:
    catalog = build_catalog(_config_with(ModelParams.parse({})))
    entry = catalog.get(ModelRef(provider="deepseek", model="deepseek-chat"))
    assert entry.cached_input_price_per_1k is None
    assert entry.reasoning_price_per_1k is None


def test_cost_estimator_uses_reasoning_price_when_present() -> None:
    estimator = CostEstimator(
        build_catalog(
            _config_with(
                ModelParams.parse(
                    {
                        "cost_per_1k_input": 1.0,
                        "cost_per_1k_output": 2.0,
                        "cost_per_1k_reasoning": 4.0,
                    }
                )
            )
        )
    )
    prices = estimator.unit_prices(ModelRef(provider="deepseek", model="deepseek-chat"))
    assert prices.reasoning_per_1k == 4.0
    usage = ModelUsage(
        input_tokens=1000,
        output_tokens=1000,
        reasoning_tokens=1000,
        total_tokens=3000,
    )
    # reasoning 有专价：1000*1 + 1000*2 + 1000*4 = 7.0
    assert estimator.estimate_cost(prices, usage) == 7.0


def test_cost_estimator_folds_reasoning_into_output_without_price() -> None:
    prices = UnitPrices(input_per_1k=1.0, output_per_1k=2.0)
    usage = ModelUsage(
        input_tokens=1000,
        output_tokens=1000,
        reasoning_tokens=1000,
        total_tokens=3000,
    )
    estimator = CostEstimator(build_catalog(LlmConfig(providers=())))
    # reasoning 无专价并入 output 计价（现行为）：1000*1 + 2000*2 = 5.0
    assert estimator.estimate_cost(prices, usage) == 5.0


def test_cached_price_falls_back_to_input_price() -> None:
    prices = UnitPrices(input_per_1k=1.0, output_per_1k=2.0)
    usage = ModelUsage(
        input_tokens=1000,
        output_tokens=0,
        cached_input_tokens=500,
        total_tokens=1000,
    )
    estimator = CostEstimator(build_catalog(LlmConfig(providers=())))
    # cached 缺失回落 input 价（现行为）：500*1 + 500*1 = 1.0
    assert estimator.estimate_cost(prices, usage) == 1.0
