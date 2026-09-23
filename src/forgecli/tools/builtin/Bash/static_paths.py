"""Conservative static directory changes and the POSIX null output sink."""

from __future__ import annotations

import os
import re
import stat

from forgecli.security.shell.command_plan import (
    CommandPlan,
    Connector,
    ParseStatus,
    ShellKind,
    UnitOrigin,
)
from forgecli.security.shell.tokens import TokenKind, tokenize_posix


def is_null_sink(target: str) -> bool:
    # Do not generalize to relative paths, aliases, variables or other devices.
    if os.name != "posix" or target != "/dev/null":
        return False
    try:
        return stat.S_ISCHR(os.stat(target).st_mode)
    except OSError:
        return False


def static_cd_targets(plan: CommandPlan) -> dict[int, str] | None:
    """Literal cd targets indexed by analysis unit, retaining shell connectors.

    The parser flattens groups and control structures. Those remain unproven;
    ordinary lists, conjunctions, disjunctions and pipelines retain their shape.
    A cd inside a pipeline runs in a subshell and is deliberately not supported.
    """
    changes = {i: u for i, u in enumerate(plan.units) if u.name == "cd"}
    if not changes:
        return {}
    if plan.shell_kind is not ShellKind.POSIX or plan.status is not ParseStatus.OK:
        return None
    control_words = {
        "if",
        "then",
        "elif",
        "else",
        "fi",
        "for",
        "while",
        "until",
        "do",
        "done",
        "case",
        "esac",
        "in",
        "select",
        "function",
        "{",
        "}",
        "!",
        "[[",
        "]]",
    }
    for token in tokenize_posix(plan.raw_command.strip()):
        if token.kind is TokenKind.OPERATOR and token.text in {"(", ")", ";;", "&"}:
            return None
        if not token.quoted and (
            token.text in control_words
            or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", token.text)
        ):
            return None
        if token.substitutions:
            return None
    if any(
        u.name in {"export", "unset", "set", "source", ".", "eval", "pushd", "popd"}
        or u.assignments
        or u.origin is not UnitOrigin.TOP_LEVEL
        for u in plan.units
    ):
        return None
    targets: dict[int, str] = {}
    pipes = {Connector.PIPE, Connector.PIPE_AMP}
    for index, unit in changes.items():
        if unit.connector in pipes or (
            index + 1 < len(plan.units) and plan.units[index + 1].connector in pipes
        ):
            return None
        if unit.executable != "cd" or not unit.raw.startswith("cd "):
            return None
        args = unit.argv[1:] if unit.argv[:1] == ("--",) else unit.argv
        if len(args) != 1:
            return None
        target = args[0]
        if (
            not target
            or target.startswith(("-", "~"))
            or any(c in target for c in "$`*?[]{}")
        ):
            return None
        targets[index] = target
    return targets
