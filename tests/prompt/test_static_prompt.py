"""提示词收敛为静态策略层之后的边界 (ADR-0042).

这一组查的都是**它不该有什么**: 不该收运行期输入, 不该出现工具名, 不该超预算, 不该每轮
重编. 四条都是那种"坏了也照常渲染"的缺陷 —— 没有用例的话, 只有模型行为会变.
"""

from __future__ import annotations

import inspect

import pytest

from forgecli.application.llm.gateway.token_estimator import (
    ApproximateTokenEstimator,
)
from forgecli.application.prompt.system_prompt_builder import (
    SystemPromptBuilder,
    _static_blocks,
)
from forgecli.application.prompt.template_renderer import PROMPT_STATIC_BUDGET
from forgecli.domain.prompt.blocks import PromptBlock, PromptBlockId
from forgecli.domain.prompt.errors import PromptBudgetExceeded
from forgecli.domain.tool.spec import ToolSpec
from support.fakes import all_tool_specs, prompt


def _spec_name(spec: ToolSpec) -> str:
    return spec.name


def test_a_block_no_longer_carries_a_cache_flag() -> None:
    """`cacheable` 与那条分区校验已经删除 (ADR-0042 决策 2).

    它们从来没生效过: 整个 system prompt 都排在 messages 之前, 块内部怎么分区都换不来
    一个字节的缓存. 留一条守着不存在的不变量的校验, 下一个读它的人会照着那条不存在的
    机制去理解.
    """
    fields = {field for field in PromptBlock.__dataclass_fields__}
    assert fields == {"block_id", "heading", "body"}


def test_the_block_order_is_fixed() -> None:
    """顺序固定, 且不因某块缺席而改变.

    身份在最前, 安全边界紧随其后: 后面每一条规则都在它划出的范围里成立.
    """
    assert [block.block_id for block in prompt().blocks] == [
        PromptBlockId.CORE_IDENTITY,
        PromptBlockId.SAFETY_RULES,
        PromptBlockId.TOOL_PROTOCOL,
        PromptBlockId.WORK_RULES,
        PromptBlockId.ANSWER_RULES,
    ]


def test_the_builder_takes_nothing_but_instructions() -> None:
    """mode / 工具目录 / 计划待办 / 记忆在类型层面就传不进来 (ADR-0042 决策 1).

    这一条不是风格: 传得进来就迟早有人传, 而那一刻提示词就重新变成每轮都要重编的东西,
    且没有任何一层会报错.
    """
    parameters = list(inspect.signature(SystemPromptBuilder.build).parameters)
    assert parameters == ["self", "instructions"]


def test_the_static_blocks_are_compiled_once_per_process() -> None:
    """内置五块只依赖包版本, 所以一个进程编译一次 (ADR-0042 决策 1)."""
    _static_blocks.cache_clear()
    before = _static_blocks.cache_info()
    SystemPromptBuilder().build()
    SystemPromptBuilder().build()
    after = _static_blocks.cache_info()
    assert before.misses == 0
    assert after.misses == 1
    assert after.hits == 1


def test_the_static_prompt_fits_the_budget() -> None:
    """预算是构建期强制的, 而且**只能下调** (ADR-0042 决策 4).

    这里断言的是相等而不是小于等于: 实测值比预算低, 说明预算还没跟着降下来, 而一个留着
    余量的棘轮在余量用完之前都不生效 —— 那段时间正是提示词长得最快的时候.
    """
    text = "\n\n".join(block.render() for block in _static_blocks())
    assert ApproximateTokenEstimator().estimate_text(text) == PROMPT_STATIC_BUDGET


def test_going_over_the_budget_fails_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """超预算是**构建期失败**, 不是警告也不是截断 (ADR-0042 决策 4).

    换成警告的话, 预算就只是一句建议 —— 而提示词的腐化方式永远是"再加一句应该没事",
    一句一句加, 没有一次会被拦下.
    """
    import forgecli.application.prompt.system_prompt_builder as builder

    monkeypatch.setattr(builder, "PROMPT_STATIC_BUDGET", 10)
    _static_blocks.cache_clear()
    try:
        with pytest.raises(PromptBudgetExceeded, match="超出预算 10"):
            SystemPromptBuilder().build()
    finally:
        # 缓存里留着一次失败的调用会让后面的用例读到同一个异常.
        _static_blocks.cache_clear()


@pytest.mark.parametrize("spec", all_tool_specs(), ids=_spec_name)
def test_the_prompt_never_names_a_tool(spec: ToolSpec) -> None:
    """提示词正文里不出现任何工具名 (ADR-0042 决策 5).

    tool schema 已经带了 name, description 与 parameters, 那就是模型选工具的全部依据.
    提示词再列一份, 就有了第二份会漂的真相.
    """
    assert spec.name not in prompt().text


# 从提示词里删掉的"检索顺序"八条, 逐条下沉到会被误用的那个工具 (ADR-0042 决策 5).
#
# 键是工具名, 值是那句话里必须留下的关键片段. 不比对整句: 措辞可以改, 但**这件事有没有
# 被说出来**不能丢 —— 删分流段时最容易发生的就是顺手把内容也丢了, 而那不会报错.
_SUNK_DISAMBIGUATION = {
    "search_text": ("find_definition", "分不出哪一处是定义"),
    "fs_find": ("路径", "search_text"),
    "fs_read": ("已经知道是哪个文件", "search_text", "offset"),
    "find_definition": ("引用", "search_text", "只写**符号名本身**"),
    "shell_run": ("专用工具",),
}


@pytest.mark.parametrize("tool_name", sorted(_SUNK_DISAMBIGUATION))
def test_the_sunk_disambiguation_survives_in_the_description(tool_name: str) -> None:
    """被下沉的每一条消歧说明都还在.

    提示词里那一节删掉了, 内容没有丢 —— 只是换了个地方: 一处, 且模型正考虑用那个工具的
    时候才读到. 这一条守的就是"换地方"没有变成"丢掉".
    """
    spec = next(item for item in all_tool_specs() if item.name == tool_name)
    for fragment in _SUNK_DISAMBIGUATION[tool_name]:
        assert fragment in spec.description, (
            f"{tool_name} 的 description 里少了 {fragment!r}; "
            "它原先在提示词的检索顺序那一节里"
        )


def test_the_prompt_module_no_longer_knows_about_runtime_facts() -> None:
    """`application/prompt` 只渲染静态策略 (ADR-0042 决策 1 / 决策 7).

    运行事实每轮可能变, 它留在这一层就意味着提示词还得每轮重编. 这一条守的是模块边界:
    一个 import 溜回来不会报错, 只会让"进程级编译一次"悄悄退回每轮一次.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src" / "forgecli" / "application"
    offenders = [
        path.name
        for path in (root / "prompt").rglob("*.py")
        if "RuntimeFacts" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


_REMOVED_CATEGORIES = (
    "定位 · 按符号",
    "定位 · 按内容",
    "定位 · 按文件名",
    "locate_symbol",
    "locate_text",
    "locate_path",
)


@pytest.mark.parametrize("label", _REMOVED_CATEGORIES)
def test_the_prompt_never_names_a_tool_category(label: str) -> None:
    """也不出现工具分类 (ADR-0042 决策 6).

    `ToolAction` 已经删了. 分类词如果还留在正文里, 它就成了提示词与 description 之间一个
    需要逐字节对齐的 join key —— 而漂了不会报错, 只表现为模型在 tools 里找不到提示词说的
    那个组, 开始瞎猜.
    """
    assert label not in prompt().text
