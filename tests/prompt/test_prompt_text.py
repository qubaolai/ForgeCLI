"""Forge 撰写给模型读的全部正文的快照与体检 (ADR-0031).

三条断言, 各挡一类事:

1. **指纹** —— 任何一个字改了都会让它变. 改了必须同时升 PROMPT_TEXT_VERSION,
   否则这里红. 提示词变了模型行为就会变, 这件事不该悄悄发生.
2. **无孤儿** —— 每个常量都要有人用 (ADR-0028 规则 C). 一段没人引用的提示词读起来
   像是生效的, 实际上模型一个字都看不到.
3. **半角标点** —— 回答契约自己写着"标点用半角". 我们要求模型做的事, 自己在同一段
   对话里必须先做到; 否则那条指令就成了噪声.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from forgecli.domain.prompt import text as prompt_text

_SRC = Path(__file__).resolve().parents[2] / "src" / "forgecli"
_FULL_WIDTH = "，。；：？！（）【】、“”‘’"


def _constants() -> dict[str, object]:
    return {
        name: value
        for name, value in vars(prompt_text).items()
        if name.isupper() and not name.startswith("_")
    }


def test_the_text_is_pinned_by_fingerprint() -> None:
    """改任何一段正文都要在这里显式承认一次."""
    assert prompt_text.PROMPT_TEXT_VERSION == 9
    assert prompt_text.fingerprint() == (
        "sha256:35a67fd730910fef002fc149dbbff8e7435c465f0e04b7834d6a86ba9762b478"
    )


def test_every_constant_has_a_consumer() -> None:
    """没人引用的提示词等于没写, 但读起来像是写了 (ADR-0028 规则 C)."""
    sources = "\n".join(
        path.read_text("utf-8")
        for path in _SRC.rglob("*.py")
        if "__pycache__" not in path.parts and path.name != "text.py"
    )
    orphans = [
        name
        for name in _constants()
        if name != "PROMPT_TEXT_VERSION" and f"prompt_text.{name}" not in sources
    ]
    assert orphans == []


@pytest.mark.parametrize("name", sorted(_constants()))
def test_the_punctuation_is_half_width(name: str) -> None:
    """我们要求模型用半角, 自己注入的文字就不能用全角."""
    value = _constants()[name]
    text = value if isinstance(value, str) else ""
    hits = sorted({char for char in text if char in _FULL_WIDTH})
    assert hits == [], f"{name} 含全角标点 {hits}"


def test_no_placeholder_is_left_unfilled() -> None:
    """带 {} 的模板必须有人 .format —— 漏一个占位符, 模型读到的就是 `{count}` 本身."""
    sources = "\n".join(
        path.read_text("utf-8")
        for path in _SRC.rglob("*.py")
        if "__pycache__" not in path.parts and path.name != "text.py"
    )
    for name, value in sorted(_constants().items()):
        if not isinstance(value, str) or not re.search(r"\{\w+\}", value):
            continue
        assert f"prompt_text.{name}.format(" in sources, f"{name} 有占位符但无人 format"
