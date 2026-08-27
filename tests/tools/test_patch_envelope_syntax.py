r"""补丁信封的动词与转义 (ADR-0029 规则三 C 类).

原来的规则是"动词认不出来就当正文". 代价是模型把自己发明的 `*** ADD` / `*** INSERT`
写进信封时, 那一行连同它后面的全部内容被原样落盘, 而工具照样回"已应用 1 处改动" ——
一次真实会话里 7 次调用中招, 5 个文件里躺着一行 `*** INSERT`, 其中两个是 Spring 启动
时要执行的 SQL 脚本.

歧义不能靠猜, 两个方向都要有出路: 打错的动词当场报错并给出合法动词表, 真的是正文就
按转义写。
"""

from __future__ import annotations

import pytest

from forgecli.application.tools.builtin.patch_envelope import (
    NewFile,
    PatchSyntaxError,
    UpdateFile,
    parse_envelope,
)


def test_an_invented_verb_is_an_error_not_content() -> None:
    with pytest.raises(PatchSyntaxError) as caught:
        parse_envelope("*** NEW a.sql\n*** INSERT\nINSERT INTO t VALUES (1);")

    assert "*** INSERT" in caught.value.described()
    assert "*** NEW" in caught.value.described()


def test_an_invented_verb_after_replace_does_not_get_swallowed() -> None:
    """`_next_marker` 找不到下一个标记时正文一路吞到信封结尾.

    于是 `*** ADD` 那一行连同它后面的方法, 整段变成了替换文本写进 .java.
    """
    with pytest.raises(PatchSyntaxError):
        parse_envelope(
            "*** UPDATE a.java\n*** FIND\nold();\n*** REPLACE\nnew();\n"
            "*** ADD\nextra();"
        )


def test_the_codex_spelling_is_rejected_too() -> None:
    """`*** Add File:` 是另一套补丁格式的写法, 模型很容易照它的训练记忆写出来."""
    with pytest.raises(PatchSyntaxError):
        parse_envelope("*** Add File: a.py\nprint(1)")


@pytest.mark.parametrize(
    "body",
    [
        "*** 注意 这一行是正文",
        "***",
        "*** ---",
    ],
)
def test_lines_that_are_not_verb_attempts_stay_content(body: str) -> None:
    """判据是"第一个词全由 ASCII 字母组成": 中文与符号一律当正文."""
    section = parse_envelope(f"*** NEW a.md\n{body}\n尾巴")[0]

    assert isinstance(section, NewFile)
    assert section.content == f"{body}\n尾巴"


def test_an_escaped_marker_lands_verbatim() -> None:
    """文件里真有以 `*** ` 开头的行时的出路."""
    section = parse_envelope("*** NEW a.md\n\\*** ADD\n尾巴")[0]

    assert isinstance(section, NewFile)
    assert section.content == "*** ADD\n尾巴"


def test_escaping_survives_a_replacement_body() -> None:
    section = parse_envelope("*** UPDATE a.md\n*** FIND\nold\n*** REPLACE\n\\*** ADD")[
        0
    ]

    assert isinstance(section, UpdateFile)
    assert section.replacements[0].replace == "*** ADD"


def test_a_literal_backslash_marker_needs_two_backslashes() -> None:
    section = parse_envelope("*** NEW a.md\n\\\\*** ADD")[0]

    assert isinstance(section, NewFile)
    assert section.content == "\\*** ADD"
