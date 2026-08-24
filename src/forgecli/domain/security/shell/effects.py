"""一条命令对文件系统做什么 (ADR-0013 §6.2, ADR-0028 规则 D).

命令知识本身在 `commands.py` —— 一条命令一条记录. 这里只做**判定**: 查表, 再处理两类
表查不出来的情形.

- **取决于参数才写.** `sed` 不写, `sed -i` 写; `sort` 不写, `sort -o f` 写;
  `perl` 跑代码, `perl -i` 就地改写. 记在 `CommandFacts.write_flags` 里按参数复判,
  而不是把整个命令归到某一边.
- **取决于子命令.** `git status` 只读, `git commit` 写.
"""

from __future__ import annotations

from forgecli.domain.security.shell.command_plan import CommandUnit
from forgecli.domain.security.shell.commands import (
    GIT_LIKE,
    GIT_READ_ONLY_SUBCOMMANDS,
    EffectKind,
    facts_for,
)

__all__ = [
    "EffectKind",
    "effect_kind_of",
    "runs_arbitrary_code",
]


def effect_kind_of(unit: CommandUnit) -> EffectKind:
    """这个单元的位置参数属于哪一类. 认不出来一律 UNPROVEN."""
    if unit.name in GIT_LIKE:
        return EffectKind.READ if _git_is_read_only(unit.argv) else EffectKind.WRITE
    facts = facts_for(unit.name)
    if _writes_in_place(facts.write_flags, unit.argv):
        return EffectKind.WRITE
    return facts.effect


def runs_arbitrary_code(unit: CommandUnit) -> bool:
    """这个单元是否会执行任意代码. 脚本分析与分类器据此决定要不要接手."""
    return effect_kind_of(unit) is EffectKind.RUNS_CODE


def _writes_in_place(triggers: tuple[str, ...], argv: tuple[str, ...]) -> bool:
    """给了写入选项吗. 短选项按字符看, 聚合写法 (`sed -ni`) 同样命中.

    triggers 为空表示这条命令没有"取决于参数"这回事 —— 无条件写的命令直接登记成
    effect=WRITE, 不走这里.
    """
    if not triggers:
        return False
    long_flags = {flag for flag in triggers if flag.startswith("--")}
    short_chars = {flag for flag in triggers if not flag.startswith("--")}
    for arg in argv:
        if arg == "--":
            break
        if arg.startswith("--"):
            if arg.split("=", 1)[0] in long_flags:
                return True
            continue
        if arg.startswith("-") and any(char in arg[1:] for char in short_chars):
            return True
    return False


def _git_is_read_only(argv: tuple[str, ...]) -> bool:
    """第一个位置参数就是子命令. 取不到子命令时按写处理 —— 裸 `git` 不该被当成只读."""
    for arg in argv:
        if arg.startswith("-"):
            continue
        return arg in GIT_READ_ONLY_SUBCOMMANDS
    return False
