"""工具结果分三段 (ADR-0041 决策 6 / 决策 9).

窗口增长的主项就是工具结果正文: 摘要与结构化字段加起来约三百 token 且可预测, 全部方差
都来自正文. 这一组查的是那条分界线有没有守住 —— 守不住不会报错, 只会让每一轮都变贵.
"""

from __future__ import annotations

import pytest

from forgecli.domain.tool.result import (
    ContentPart,
    ResultProvenance,
    ToolError,
    ToolResult,
    ToolResultStatus,
)
from forgecli.domain.tool.spec import ToolSpec
from support.fakes import all_tool_specs


def _spec_name(spec: ToolSpec) -> str:
    return spec.name


def _result(**overrides: object) -> ToolResult:
    fields: dict[str, object] = {
        "invocation_id": "inv-1",
        "tool_name": "fs_read",
        "status": ToolResultStatus.OK,
        "summary": "读了 a.py, 92 行, 未截断",
        "data": {"path": "a.py", "lines": 92},
        # 超过 _INLINE_ALWAYS_BELOW_CHARS: 小正文一律直接进窗口, 拿它测不出"换句柄".
        "content_parts": (ContentPart(text="第一行\n第二行\n" + "x" * 8_000),),
    }
    fields.update(overrides)
    return ToolResult(**fields)  # type: ignore[arg-type]


# ---- 必填的摘要 ----


def test_a_result_without_a_summary_is_rejected() -> None:
    """摘要必填.

    漏填的后果是模型看到一条没有结论的结果 —— 那不会报错, 只会让它多调一次工具去问
    同一件事. 让构造期就拦住.
    """
    with pytest.raises(ValueError, match="summary 不能为空"):
        _result(summary="   ")


@pytest.mark.parametrize("spec", all_tool_specs(), ids=_spec_name)
def test_every_tool_declares_whether_its_body_enters_the_window(spec: ToolSpec) -> None:
    """`body_in_window` 是逐工具的显式声明, 不是全局默认.

    参数化到每一个工具: 加一个新工具时, 这条会逼作者想一次"它的正文要不要每轮重发".
    """
    assert isinstance(spec.body_in_window, bool)


def test_only_two_tools_always_bring_their_body_into_the_window() -> None:
    """只有两个工具无条件带正文.

    `fs_read`: 改代码之前必须看到原文, 而"先 artifact_read 取回来再改"是白花一次调用.
    `artifact_read`: 它**就是**取回路径 —— 不带正文的话它会回一个指向同一份归档的句柄,
    而模型刚做的就是这件事, 一个自指的循环.

    其余工具走 `render_for_model` 的两条兜底: 没句柄必须带, 小正文一律带.
    """
    carriers = sorted(spec.name for spec in all_tool_specs() if spec.body_in_window)
    assert carriers == ["artifact_read", "fs_read"]


# ---- 两个出口 ----


def test_the_raw_output_keeps_the_whole_body() -> None:
    """围栏扫的是命令**实际输出**, 不是摘要 (ADR-0041 决策 9).

    给它摘要等于把这道防线关掉: 那句 `Permission denied` 只出现在正文里.
    """
    result = _result(
        status=ToolResultStatus.TOOL_ERROR,
        error=ToolError(code="failed", message="Permission denied: /etc/passwd"),
    )
    assert "第一行" in result.raw_output()
    assert "Permission denied: /etc/passwd" in result.raw_output()


def test_a_large_body_is_replaced_by_a_handle() -> None:
    """大正文换成句柄, 只留摘要与结构化字段."""
    result = _result(
        provenance=ResultProvenance(artifact_id="7f3a1b2c", byte_size=8_100)
    )
    rendered = result.render_for_model(include_body=False)
    assert "读了 a.py, 92 行, 未截断" in rendered
    assert "lines=92" in rendered
    assert "第一行" not in rendered


def test_a_small_body_goes_in_even_when_the_tool_says_no() -> None:
    """小正文一律直接进窗口 (回归).

    省下一段 300 token 的正文, 换来的是模型多发一次 artifact_read —— 而那一次往返要把
    整个上下文重发一遍. 省小的花大的, 净效果是负的.
    """
    result = _result(
        content_parts=(ContentPart(text="a.py:44: offset"),),
        provenance=ResultProvenance(artifact_id="7f3a1b2c", byte_size=15),
    )
    assert "a.py:44: offset" in result.render_for_model(include_body=False)


def test_a_body_with_no_handle_goes_in_regardless() -> None:
    """没有句柄就必须带正文 (回归).

    计划, 待办与记忆那几个工具都不归档. 少了这一条, `plan_read` 回给模型的只有一句
    "读取计划", 正文凭空消失且无从取回 —— 而 `artifact_read` 会变成一个取不回东西的
    取回工具.
    """
    result = _result(summary="读取计划", provenance=None)
    rendered = result.render_for_model(include_body=False)
    assert "第一行" in rendered


def test_the_model_view_keeps_the_body_when_the_tool_asks_for_it() -> None:
    rendered = _result().render_for_model(include_body=True)
    assert "第一行" in rendered


def test_withholding_the_body_always_hands_over_a_handle() -> None:
    """换掉正文就必须给句柄, 否则内容等于被删了还不告诉人去哪找."""
    result = _result(
        provenance=ResultProvenance(artifact_id="7f3a1b2c", byte_size=8_100)
    )
    rendered = result.render_for_model(include_body=False)
    assert "7f3a1b2c" in rendered
    assert "artifact_read" in rendered


def test_a_failure_always_carries_its_reason() -> None:
    """失败一律带上原因: 模型据此改方案, 而"退出 1"本身说明不了改什么."""
    result = _result(
        status=ToolResultStatus.TOOL_ERROR,
        summary="pytest -> 退出 1",
        error=ToolError(code="failed", message="AssertionError: 期望 3, 实得 4"),
        provenance=ResultProvenance(artifact_id="7f3a1b2c", byte_size=8_100),
    )
    rendered = result.render_for_model(include_body=False)
    assert "AssertionError: 期望 3, 实得 4" in rendered
