"""ApproximateTokenEstimator（ADR-0011 §11.4，2026-07-06 切片）。

近似估算：确定性、只读、覆盖 system prompt / messages / tools schema。
"""

from forgecli.application.llm.gateway import (
    ApproximateTokenEstimator,
    ChatMessage,
    TextBlock,
    ToolSpec,
)
from forgecli.domain.conversation import MessageRole


def _msg(text: str) -> ChatMessage:
    return ChatMessage(role=MessageRole.USER, content=(TextBlock(text),))


def test_empty_text_is_zero() -> None:
    assert ApproximateTokenEstimator().estimate_text("") == 0


def test_short_text_estimates_at_least_one_token() -> None:
    assert ApproximateTokenEstimator().estimate_text("hi") == 1


def test_text_estimate_scales_with_length() -> None:
    estimator = ApproximateTokenEstimator()
    assert estimator.estimate_text("x" * 400) == 100


def test_message_estimate_includes_per_message_overhead() -> None:
    estimator = ApproximateTokenEstimator()
    single = estimator.estimate_input(messages=(_msg("x" * 40),))
    double = estimator.estimate_input(messages=(_msg("x" * 40), _msg("x" * 40)))
    assert double == single * 2


def test_system_prompt_and_tools_are_counted() -> None:
    estimator = ApproximateTokenEstimator()
    bare = estimator.estimate_input(messages=(_msg("hello"),))
    with_system = estimator.estimate_input(
        messages=(_msg("hello"),), system_prompt="you are helpful" * 10
    )
    with_tools = estimator.estimate_input(
        messages=(_msg("hello"),),
        tools=(ToolSpec(name="read_file", description="读取文件内容" * 10),),
    )
    assert with_system > bare
    assert with_tools > bare


def test_estimate_is_deterministic() -> None:
    estimator = ApproximateTokenEstimator()
    args = {"messages": (_msg("同样的输入"),), "system_prompt": "sys"}
    assert estimator.estimate_input(**args) == estimator.estimate_input(**args)
