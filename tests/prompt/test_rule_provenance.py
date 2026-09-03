"""每条规则都要能指认一个具体的错误动作 (ADR-0042 决策 3).

判据是一句话:

> 删掉这一句, **哪个具体的错误动作**会变得可能?

答不上来的规则就不该加. 判据写在规则行前的 `{# why: … #}` 里 —— 注释不进指纹, 所以补一条
来源不需要升版本, 也就不会因为摩擦而没人写.

这道门查的是**有没有**, 不是写得好不好. 后者是人的事; 但"一条规则悄悄溜进提示词, 没人说
得出它防的是什么"这件事, 机器挡得住.
"""

from __future__ import annotations

import re

import pytest

from forgecli.application.prompt.template_renderer import (
    TEMPLATE_SUFFIX,
    template_names,
    template_source,
)

_BLOCK_TEMPLATES = tuple(
    name for name in template_names() if name.startswith("blocks/")
)
# 规则行: 顶格的 "- ". 续行 (缩进的延续) 不算 —— 它们属于上一条.
_RULE = re.compile(r"^- ")
_WHY = re.compile(r"\{#\s*why:")


def _rules_without_why(source: str) -> list[str]:
    """逐行走一遍, 记下每条规则行前面最近的注释是不是 why."""
    missing = []
    seen_why = False
    for line in source.split("\n"):
        if _WHY.search(line):
            seen_why = True
            continue
        if _RULE.match(line):
            if not seen_why:
                missing.append(line.strip())
            seen_why = False
            continue
        # 只有空行与小标题可以夹在 why 与它的规则之间; 别的东西出现就说明那条 why
        # 已经被用掉了或者根本不是给这条规则的.
        if line.strip() and not line.startswith(("#", "{%", "{#")):
            seen_why = False
    return missing


@pytest.mark.parametrize("name", _BLOCK_TEMPLATES)
def test_every_rule_records_the_failure_it_prevents(name: str) -> None:
    missing = _rules_without_why(template_source(name))
    assert not missing, (
        f"{name} 里这些规则没有 {{# why: #}}: {missing}. "
        "写清删掉它之后哪个具体的错误动作会变得可能; 答不上来就不该加这条."
    )


def test_the_gate_actually_catches_a_missing_why() -> None:
    """反向用例: 没有这一条, 上面那个断言写错了也永远绿.

    一道自己不会失败的门比没有门更糟 —— 它让人以为规则已经被守着了.
    """
    assert _rules_without_why("- 一条没有来源的规则") == ["- 一条没有来源的规则"]
    assert _rules_without_why("{# why: 有来源 #}\n- 一条规则") == []


def test_every_block_template_is_covered() -> None:
    """块模板一份都不能漏.

    参数化列表是在导入时算出来的, 空列表会让整个文件"全绿"而其实什么都没查.
    """
    assert len(_BLOCK_TEMPLATES) == 6
    assert all(name.endswith(TEMPLATE_SUFFIX) for name in _BLOCK_TEMPLATES)
