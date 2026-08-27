"""Forge 撰写给模型读的全部正文, 以及渲染它的那一个函数 (ADR-0039).

## 一个家, 三种形状

`templates/` 是**唯一**的正文出处:

    blocks/<block_id>.md.j2     系统提示词的块正文. 有骨架: 哪几节, 什么条件下出现
    headings.md.j2              九个块的标题, 一张表
    notices/<group>.md.j2       回合中的引导, 收摊通知, 工具回执, 压缩占位

原先这些文字分两处: 块正文靠 `SystemPromptBuilder` 用六个常量拼, 其余的当常量摆在
`domain/prompt/text.py`. 两处都不对劲 —— 前者的骨架只存在于控制流里, 读两个文件才知道
模型看到什么; 后者坐在 domain 却没有一个 domain 消费方, 八个消费方全在 application.

**按组存, 不按条存.** 五十几句话拆成五十几个文件, 找是好找了, 但"这几句彼此矛盾没有"
就再也看不出来了 —— 而那正是 ADR-0031 当初收拢正文要解决的问题. 所以一组一份文件,
一句话一个 `{% raw %}{% macro %}{% endraw %}`: `render_notice("loop.halt")` 里的
`loop` 是文件, `halt` 是宏.

## 四条边界

- **读一次, 进程内冻结.** 模板在会话中途被改写会让同一轮的提示词漂, 而 ADR-0018 要求提示
  词按 turn 冻结. `auto_reload=False` 与 `lru_cache` 一起把这件事钉死.
- **缺文件当场炸.** 与 FORGE.md 那条"读不到就跳过"相反: 内置正文不是可选的, 少一份模板就
  是安装坏了, 悄悄渲染出一句空话比起不来更糟.
- **`StrictUndefined`.** 漏一个槽位要抛异常, 不能静默渲染成空字符串 —— 那正是提示词
  类缺陷的典型形态: 没有任何一层会说话, 只是模型少读到一段.
- **`{# #}` 注释不进指纹.** 每份模板的注释里写着它为什么这么措辞, 那是这套正文最值钱的
  部分. 让改一句注释也要升版本, 结果就是没人写注释.

模板与渲染器都在 application 而不是 infrastructure: 它们是**包内数据**, 不是工作区状态,
不随用户环境变化, 也没有降级余地. 见 ADR-0039.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from functools import lru_cache
from importlib import resources
from importlib.resources.abc import Traversable

from jinja2 import Environment, FunctionLoader, StrictUndefined

from forgecli.domain.prompt.blocks import PromptBlockId
from forgecli.domain.tool.hashing import digest_text

__all__ = [
    "PROMPT_TEXT_VERSION",
    "TEMPLATE_SUFFIX",
    "render_block",
    "render_heading",
    "render_notice",
    "template_names",
    "template_source",
    "templates_fingerprint",
]

# 改任何一份模板都要升它, 并更新 tests/prompt/test_prompt_text.py 的指纹. 提示词变了模型
# 行为就会变, 这件事必须是显式的 (ADR-0018 §15.3).
#
# 接 MAIN_AGENT_PROMPT_VERSION 的 5 与 PROMPT_TEXT_VERSION 的 10 往下数: 三个常量数的是
# 同一件事 (Forge 撰写的正文改了没有), 只是管辖范围一次比一次大.
PROMPT_TEXT_VERSION = 12

TEMPLATE_SUFFIX = ".md.j2"
_ANCHOR = "forgecli.application.prompt"
_DIRECTORY = "templates"
_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)


def template_names() -> tuple[str, ...]:
    """模板目录里的全部文件, 相对路径, 排序固定.

    排序固定是因为它进指纹: 目录遍历顺序在不同文件系统上不一样, 拿它算哈希会让同样的
    模板在不同机器上得出不同的指纹.
    """
    return tuple(sorted(_walk(_root(), "")))


def template_source(name: str) -> str:
    """一份模板的正文. 走 importlib.resources, 与打包后的读法完全一致."""
    return _root().joinpath(*name.split("/")).read_text(encoding="utf-8")


def templates_fingerprint() -> str:
    """全部模板的指纹, 不含 `{# #}` 注释.

    注释是给人读的, 一个字都不会进模型上下文; 把它算进去, 改一句"为什么这么措辞"也要升
    版本重钉指纹, 而那种摩擦的唯一后果是没人再写注释.
    """
    parts = [
        f"{name}\n{_COMMENT.sub('', template_source(name))}"
        for name in template_names()
    ]
    return digest_text("\n".join(parts))


def render_block(block_id: PromptBlockId, /, **context: object) -> str:
    """渲染一个系统提示词块的正文. 模板文件名即 block_id 的取值.

    复用 `PromptBlockId` 当模板标识, 不另立一份模板 id 枚举: 那会是第二份要手工同步的
    表, 而漏同步的那一条不会报错, 只会让某个块渲染不出来.
    """
    return _render(f"blocks/{block_id.value}", context)


def render_heading(block_id: PromptBlockId, /) -> str:
    """块标题. 九个标题是一张表, 所以它们同住一份模板.

    与块正文分开是因为它是 `PromptBlock` 的字段, 由 `render()` 给它加 `# ` 前缀.
    """
    return _render("headings", {"block_id": block_id.value})


def render_notice(name: str, /, **context: object) -> str:
    """回合中的引导, 收摊通知, 工具回执与压缩占位.

    `name` 是 `<组>.<条>`: 组是 `notices/` 下的文件, 条是那份文件里的一个 macro. 按组存
    是为了让同一类里的几句话能一眼扫完 —— 它们最常见的毛病是彼此矛盾, 而那种毛病只有
    并排放着才看得出来.
    """
    group, _, macro = name.partition(".")
    module = _environment().get_template(f"notices/{group}{TEMPLATE_SUFFIX}").module
    render = getattr(module, macro, None)
    if render is None:
        raise LookupError(f"notices/{group}{TEMPLATE_SUFFIX} 里没有 {macro} 这一条")
    return str(render(**context)).strip()


def _render(stem: str, context: dict[str, object]) -> str:
    template = _environment().get_template(f"{stem}{TEMPLATE_SUFFIX}")
    return template.render(**context).strip()


def _root() -> Traversable:
    return resources.files(_ANCHOR).joinpath(_DIRECTORY)


def _walk(directory: Traversable, prefix: str) -> Iterator[str]:
    for entry in directory.iterdir():
        name = f"{prefix}{entry.name}"
        if entry.is_dir():
            yield from _walk(entry, f"{name}/")
        elif name.endswith(TEMPLATE_SUFFIX):
            yield name


@lru_cache(maxsize=1)
def _environment() -> Environment:
    return Environment(
        loader=FunctionLoader(template_source),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
        auto_reload=False,
        # 这不是 HTML: 转义会把提示词里的引号和尖括号变成实体.
        autoescape=False,
    )
