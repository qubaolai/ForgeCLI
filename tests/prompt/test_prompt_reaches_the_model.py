"""提示词真的到了模型手上, 且一轮之内不变 (ADR-0018 §15.2).

接线用例比 builder 单测重要: builder 编译得再对, 只要 `_build_request` 忘了设
`system_prompt`, 整套东西就等于没有 —— 而这种漏接不会报错, 只会让模型退回"没有身份,
不知道有哪些工具, 也不知道自己在什么平台上"的状态.
"""

from __future__ import annotations

from forgecli.domain.agent.actions import LoopObservation, ObservationSource
from support.loop_harness import ScriptedGateway, call, loop_input, loop_with, response


def test_the_first_model_call_carries_a_non_empty_system_prompt() -> None:
    gateway = ScriptedGateway(responses=[response("好的")])
    loop, _ = loop_with(gateway)

    loop.start(loop_input())

    assert len(gateway.requests) == 1
    prompt_text = gateway.requests[0].system_prompt
    assert prompt_text
    assert "你是运行在 ForgeCLI 中的编码 Agent" in prompt_text


def test_the_prompt_is_identical_across_every_call_in_one_turn() -> None:
    """一轮内多次模型调用复用同一份 (ADR-0018 §6.2).

    中间隔着一次工具调用: 冻结规则真正要挡的就是"工具跑完之后顺手换个提示词".
    """
    gateway = ScriptedGateway(
        responses=[
            response(tool_calls=(call("fs_read", "c1"),)),
            response("读完了"),
        ]
    )
    loop, _ = loop_with(gateway)

    loop.start(loop_input())
    loop.observe(
        LoopObservation(content="file contents", source=ObservationSource.TOOL)
    )

    assert len(gateway.requests) == 2
    first, second = gateway.requests
    assert first.system_prompt == second.system_prompt


def test_the_prompt_lists_the_same_tools_the_provider_receives() -> None:
    """提示词里的工具名与 tool schema 来自同一份 catalog 快照 (ADR-0018 §2.1)."""
    gateway = ScriptedGateway(responses=[response("好的")])
    loop, _ = loop_with(gateway)

    loop.start(loop_input())

    request = gateway.requests[0]
    assert request.system_prompt is not None
    for schema in request.tools:
        assert schema.name in request.system_prompt


def test_a_turn_without_tools_still_gets_identity_and_contract() -> None:
    gateway = ScriptedGateway(responses=[response("好的")])
    loop, _ = loop_with(gateway)

    loop.start(loop_input(with_tools=False))

    prompt_text = gateway.requests[0].system_prompt
    assert prompt_text is not None
    assert "你是运行在 ForgeCLI 中的编码 Agent" in prompt_text
    assert "不得绕过工具伪造副作用" in prompt_text


def test_closing_the_catalog_does_not_recompile_the_prompt() -> None:
    """本轮收回工具目录时只追加受控运行通知, 不重编核心提示词 (ADR-0018 §6.2)."""
    gateway = ScriptedGateway(
        responses=[
            response(tool_calls=(call("fs_read", "c1"),)),
            response("那我说明一下"),
        ]
    )
    loop, _ = loop_with(gateway)

    loop.start(loop_input())
    # HALT: 人类明确拒绝, 循环会收掉目录再调一次模型.
    from forgecli.domain.agent.actions import ObservationDisposition

    loop.observe(
        LoopObservation(
            content="用户拒绝",
            source=ObservationSource.SECURITY,
            disposition=ObservationDisposition.HALT,
        )
    )

    assert len(gateway.requests) == 2
    assert gateway.requests[0].system_prompt == gateway.requests[1].system_prompt
    # 目录确实被收掉了 —— 否则上面那条断言是在比两次相同的正常调用.
    assert gateway.requests_tools == [3, 0]
