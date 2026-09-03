"""围栏拿到的是原始输出, 不是摘要 (ADR-0041 决策 9).

这是本次改造里最容易悄悄坏掉的一处. 正文移出会话窗口之后, `render_for_model` 只给摘要与
结构化字段 —— 而 `fence_hint` 是靠**正则扫命令实际输出**里的越界痕迹工作的. 给它摘要,
那句 `Permission denied: /etc/shadow` 就不在里面了, 这道防线整个失效, 而且不会报错:
命令照常失败, 只是模型再也读不出"这是围栏拦的", 于是它会换个写法一直重试.
"""

from __future__ import annotations

from forgecli.application.tool_request.fence_hint import fence_hint
from forgecli.domain.execution.fence import FencePolicy
from forgecli.domain.tool.result import (
    ContentPart,
    ResultProvenance,
    ToolError,
    ToolResult,
    ToolResultStatus,
)

_BLOCKED = "/etc/shadow"


def _denied_result() -> ToolResult:
    """一次被围栏拦下的命令.

    命令行本身不提那个路径 —— 是脚本跑起来之后才去碰的. 这是最常见的形态: `make test`,
    `./build.sh`, 一个依赖装脚本, 命令行上什么都看不出来, 痕迹只在 stderr 里.
    """
    return ToolResult(
        invocation_id="inv-1",
        tool_name="shell_run",
        status=ToolResultStatus.OK,
        summary="./build.sh -> 退出 1, 48 字节输出, 0.2s",
        data={"exit_code": 1},
        # 正文够大才会被换成句柄 —— 小正文一律直接进窗口, 那种情况下摘要与正文都在,
        # 测不出"围栏看的是哪一份".
        content_parts=(
            ContentPart(text=f"cp: {_BLOCKED}: Permission denied\n" + "log\n" * 2_000),
        ),
        provenance=ResultProvenance(artifact_id="c305ff22", byte_size=8_100),
    )


def _fence() -> FencePolicy:
    return FencePolicy(writable_roots=("/ws",))


def test_the_raw_output_still_carries_the_denial() -> None:
    result = _denied_result()
    assert _BLOCKED in result.raw_output()


def test_the_model_view_does_not() -> None:
    """摘要里没有那条路径 —— 这正是不能拿它去扫的原因."""
    result = _denied_result()
    assert _BLOCKED not in result.render_for_model(include_body=False)


def test_the_fence_recognises_the_denial_from_the_raw_output() -> None:
    hint = fence_hint(_denied_result().raw_output(), _fence(), confined=True)
    assert hint, "围栏应当认出这次拒绝"
    assert _BLOCKED in hint


def test_the_fence_sees_nothing_in_the_model_view() -> None:
    """反向用例: 如果哪天有人把这里换成 render_for_model, 这一条会失败.

    没有它, 那次替换在整个套件里都是绿的 —— 因为围栏"没认出越界"与"确实没有越界"在
    断言上长得一模一样.
    """
    summary_only = _denied_result().render_for_model(include_body=False)
    assert fence_hint(summary_only, _fence(), confined=True) == ""


def test_a_failure_message_reaches_the_raw_output_too() -> None:
    """一次 mkdir 失败根本没有输出, 痕迹只在 error.message 里.

    只拼 content_parts 的话, 这类失败在围栏眼里就是一段空字符串.
    """
    result = ToolResult(
        invocation_id="inv-2",
        tool_name="shell_run",
        status=ToolResultStatus.TOOL_ERROR,
        summary="mkdir -> 退出 1",
        error=ToolError(code="failed", message=f"mkdir: {_BLOCKED}: Permission denied"),
    )
    assert _BLOCKED in result.raw_output()
