"""模板目录的健康检查与指纹钉子 (ADR-0031, ADR-0039, ADR-0042).

这几条都是**目录驱动**的: 新增一份模板不需要回来改用例, 它自动被覆盖. 手写一张文件清单
的话, 漏登记的那一份就永远不被检查, 而那正是提示词类缺陷的典型形态.
"""

from __future__ import annotations

import re

import pytest

from forgecli.application.prompt.template_renderer import (
    PROMPT_TEXT_VERSION,
    TEMPLATE_SUFFIX,
    render_heading,
    template_names,
    template_source,
    templates_fingerprint,
)
from forgecli.domain.prompt.blocks import PromptBlockId

_MACRO = re.compile(r"\{%\s*macro\s+([a-z_]+)\(")
_FULL_WIDTH = "，。；：？！（）【】、“”‘’"


def _stems(prefix: str) -> set[str]:
    return {
        name[len(prefix) : -len(TEMPLATE_SUFFIX)]
        for name in template_names()
        if name.startswith(prefix)
    }


def test_blocks_match_the_block_ids() -> None:
    """`blocks/` 的文件名就是 PromptBlockId 的取值, 两边必须完全对上.

    多一份是渲染不到的死文本, 少一份是渲染时才炸的缺口.
    """
    assert _stems("blocks/") == {block_id.value for block_id in PromptBlockId}


def test_every_block_has_a_heading() -> None:
    """标题表缺一个键不会静默渲染成空 —— StrictUndefined 会抛. 这里把它变成用例."""
    for block_id in PromptBlockId:
        assert render_heading(block_id).strip()


def test_runtime_and_state_are_separate_directories() -> None:
    """[4] 与 [6] 分两支 (ADR-0041).

    它们在缓存上是相反的: 运行事实进前缀, 状态帧只能待在末尾. 合成一个目录, 迟早有人
    把一份每轮都变的正文放进前缀那一支.
    """
    assert _stems("runtime/") == {"runtime_facts"}
    assert _stems("state/") == {"frame"}


def test_every_notice_macro_has_a_consumer() -> None:
    """notices/ 里的每一条都要有人调.

    没有消费方的措辞是死文本, 而它读起来与活的一模一样 —— 改错了也没人发现.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src" / "forgecli"
    code = "".join(
        re.sub(r"\s+", "", path.read_text(encoding="utf-8"))
        for path in src.rglob("*.py")
    )
    orphans = []
    for name in template_names():
        if not name.startswith("notices/"):
            continue
        group = name[len("notices/") : -len(TEMPLATE_SUFFIX)]
        for macro in _MACRO.findall(template_source(name)):
            if f'render_notice("{group}.{macro}"' not in code:
                orphans.append(f"{group}.{macro}")
    assert not orphans, f"没有消费方的 notice: {orphans}"


@pytest.mark.parametrize("name", template_names())
def test_the_punctuation_is_half_width(name: str) -> None:
    """标点一律半角.

    回答规则自己写着这一条 —— 我们要求模型做的事, 自己在同一段上下文里得先做到.
    """
    body = template_source(name)
    found = [mark for mark in _FULL_WIDTH if mark in body]
    assert not found, f"{name} 里有全角标点: {found}"


def test_the_text_is_pinned_by_fingerprint() -> None:
    """正文变了就要显式升版本并重钉指纹 (ADR-0018 §15.3).

    这一条钉的是**措辞**; 块的构成与顺序由 test_static_prompt 那边的快照指纹钉.
    两个分开是因为它们坏掉的原因不同: 改一句话与改一层结构该分别看得见.
    """
    assert PROMPT_TEXT_VERSION == 20
    assert templates_fingerprint() == (
        "sha256:eee767c2d022e27d5c055f6b47aa9482313bc5c974547ac40ec793b31f6022ea"
    )
