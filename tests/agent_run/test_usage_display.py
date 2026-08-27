"""每次模型调用后的 token 用量展示 (ADR-0016 §5).

三件事要成立: 每次调用都报, 累计按 turn 清零, 估算值不伪装成实测值.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from forgecli.application.agent_run.events import AgentRunEventBus
from forgecli.domain.agent.run_events import (
    AgentRunEventKind,
    ContextCompactedPayload,
    ModelUsagePayload,
    RunEventPayload,
    TurnFinishedPayload,
)
from forgecli.interfaces.cli.run_renderer import TerminalRunRenderer

K = AgentRunEventKind


class Harness:
    def __init__(self) -> None:
        self.console = Console(
            file=io.StringIO(),
            width=120,
            record=True,
            no_color=True,
            force_terminal=False,
        )
        self.renderer = TerminalRunRenderer(self.console)
        self.bus = AgentRunEventBus()
        self.bus.subscribe(self.renderer)

    def send(self, kind: AgentRunEventKind, payload: RunEventPayload) -> None:
        self.bus.publish(kind, turn_id="turn-1", payload=payload)

    def text(self) -> str:
        return self.console.export_text()


@pytest.fixture
def harness() -> Harness:
    return Harness()


# ---- 单次调用 ----


def test_usage_is_reported_after_the_call(harness: Harness) -> None:
    with harness.renderer.turn():
        harness.send(
            K.MODEL_USAGE, ModelUsagePayload(input_tokens=1234, output_tokens=567)
        )

    text = harness.text()
    # 过千换 k (ADR-0037): 这个数字是拿来判断量级的, 不是拿来对账的.
    assert "输入 1.2k" in text
    assert "输出 567" in text


def test_the_first_call_does_not_repeat_itself_as_a_running_total(
    harness: Harness,
) -> None:
    """第一次调用时单次量与累计量必然相等, 打两遍只是噪音."""
    with harness.renderer.turn():
        harness.send(
            K.MODEL_USAGE, ModelUsagePayload(input_tokens=100, output_tokens=50)
        )

    assert "本轮累计" not in harness.text()


def test_cached_and_reasoning_tokens_only_show_when_present(
    harness: Harness,
) -> None:
    with harness.renderer.turn():
        harness.send(
            K.MODEL_USAGE, ModelUsagePayload(input_tokens=100, output_tokens=50)
        )
    assert "缓存" not in harness.text()
    assert "思考" not in harness.text()

    second = Harness()
    with second.renderer.turn():
        second.send(
            K.MODEL_USAGE,
            ModelUsagePayload(
                input_tokens=100,
                output_tokens=50,
                cached_tokens=80,
                reasoning_tokens=40,
            ),
        )
    text = second.text()
    assert "其中缓存 80" in text
    assert "思考 40" in text


# ---- 累计 ----


def test_later_calls_carry_the_running_total(harness: Harness) -> None:
    with harness.renderer.turn():
        harness.send(
            K.MODEL_USAGE, ModelUsagePayload(input_tokens=100, output_tokens=50)
        )
        harness.send(
            K.MODEL_USAGE, ModelUsagePayload(input_tokens=200, output_tokens=30)
        )

    assert "本轮累计 380" in harness.text()


def test_the_provider_total_wins_over_adding_the_parts(harness: Harness) -> None:
    """各家对"总数"口径不同 (reasoning 有的并进 output). 自己加会和账单对不上."""
    with harness.renderer.turn():
        harness.send(
            K.MODEL_USAGE,
            ModelUsagePayload(
                input_tokens=100,
                output_tokens=50,
                reasoning_tokens=40,
                total_tokens=190,  # 供应商说是 190, 不是 150
            ),
        )
        harness.send(
            K.MODEL_USAGE, ModelUsagePayload(input_tokens=10, output_tokens=10)
        )

    assert "本轮累计 210" in harness.text()


def test_the_total_resets_between_turns(harness: Harness) -> None:
    """ "本轮消耗"要能对上用户刚提的这个问题, 不是会话开始以来的总和."""
    with harness.renderer.turn():
        harness.send(
            K.MODEL_USAGE, ModelUsagePayload(input_tokens=1000, output_tokens=1000)
        )
    with harness.renderer.turn():
        harness.send(K.MODEL_USAGE, ModelUsagePayload(input_tokens=7, output_tokens=3))
        harness.send(K.MODEL_USAGE, ModelUsagePayload(input_tokens=1, output_tokens=1))

    assert "本轮累计 12" in harness.text()
    assert "本轮累计 2,012" not in harness.text()


def test_the_turn_summary_carries_the_total(harness: Harness) -> None:
    """中途的用量行会被长回答顶上去, 收尾行得再给一次总量."""
    with harness.renderer.turn():
        harness.send(
            K.MODEL_USAGE, ModelUsagePayload(input_tokens=100, output_tokens=50)
        )
        harness.send(
            K.TURN_COMPLETED,
            TurnFinishedPayload(status="final_answer", model_calls=1, tool_calls=0),
        )

    assert "150 tokens" in harness.text()


def test_a_turn_without_usage_says_nothing_about_tokens(harness: Harness) -> None:
    """供应商完全没回 usage 时不编一个 0 出来."""
    with harness.renderer.turn():
        harness.send(
            K.TURN_COMPLETED,
            TurnFinishedPayload(status="final_answer", model_calls=1, tool_calls=0),
        )

    assert "tokens" not in harness.text()


# ---- 估算 ----


def test_estimated_usage_is_marked(harness: Harness) -> None:
    """不标出来, 用户会拿本地估算值去核供应商账单."""
    with harness.renderer.turn():
        harness.send(
            K.MODEL_USAGE,
            ModelUsagePayload(input_tokens=100, output_tokens=50, estimated=True),
        )

    assert "(估算)" in harness.text()


def test_one_estimated_call_taints_the_turn_total(harness: Harness) -> None:
    """混着报比全估算更容易误导: 总数里只要掺了估算就得说."""
    with harness.renderer.turn():
        harness.send(
            K.MODEL_USAGE, ModelUsagePayload(input_tokens=100, output_tokens=50)
        )
        harness.send(
            K.MODEL_USAGE,
            ModelUsagePayload(input_tokens=10, output_tokens=5, estimated=True),
        )
        harness.send(
            K.TURN_COMPLETED,
            TurnFinishedPayload(status="final_answer", model_calls=2, tool_calls=0),
        )

    assert "165 tokens (含估算)" in harness.text()


# ---- 上下文压缩 (ADR-0037) ----


def test_compaction_usage_is_labelled_and_counted(harness: Harness) -> None:
    """压缩是 Forge 自己发起的调用.

    不算进合计, 总数就对不上账单; 算进去却不标出来, 用户会以为自己问一句话烧了这么多.
    所以两件事都要做.
    """
    with harness.renderer.turn():
        harness.send(
            K.MODEL_USAGE,
            ModelUsagePayload(origin="act", input_tokens=2000, output_tokens=100),
        )
        harness.send(
            K.MODEL_USAGE,
            ModelUsagePayload(origin="compact", input_tokens=8000, output_tokens=200),
        )
        harness.send(
            K.TURN_COMPLETED,
            TurnFinishedPayload(
                status="completed", model_calls=1, tool_calls=0, elapsed_ms=100.0
            ),
        )

    text = harness.text()
    assert "用量(压缩)" in text
    assert "10.3k tokens (含压缩 8.2k)" in text


def test_a_compaction_row_says_what_it_saved(harness: Harness) -> None:
    """省下的是上下文, 花掉的是 token —— 两个方向相反的数, 各自一行."""
    with harness.renderer.turn():
        harness.send(
            K.CONTEXT_COMPACTED,
            ContextCompactedPayload(
                level="summary",
                tokens_before=9000,
                tokens_after=1200,
                tokens_saved=7800,
                messages_replaced=12,
            ),
        )

    text = harness.text()
    assert "上下文压缩 (摘要)" in text
    assert "省下 7.8k tokens" in text
    assert "顶替 12 条消息" in text
