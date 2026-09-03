from forgecli.application.llm.config.llm_config import (
    ModelParams,
    ModelSpec,
    ProviderConfig,
)
from forgecli.shared.serialization import to_jsonable


def test_thinking_effort_value_objects_are_serialized_as_names() -> None:
    params = ModelParams(
        thinking_efforts=["low", "high"],
        thinking_effort="high",
        thinking_default_effort="low",
    )
    provider = ProviderConfig(
        id="example",
        name="Example",
        api_base="https://example.invalid/v1/chat/completions",
        models=(ModelSpec(provider="example", id="model", params=params),),
    )

    payload = to_jsonable(provider)
    model_params = payload["models"][0]["params"]

    assert model_params["thinking_efforts"] == ["low", "high"]
    assert model_params["thinking_effort"] == "high"
    assert model_params["thinking_default_effort"] == "low"
