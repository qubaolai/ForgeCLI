"""Possible working directories along a shell list; execution stays unchanged."""

from __future__ import annotations

import os
from dataclasses import replace

from forgecli.core.tool.tool import ToolUseContext
from forgecli.security.filesystem import expand_path, path_in_allowed_working_path
from forgecli.security.shell.command_plan import CommandPlan, Connector
from forgecli.tools.builtin.Bash.static_paths import static_cd_targets


def directory_contexts(
    plan: CommandPlan, context: ToolUseContext
) -> tuple[tuple[tuple[ToolUseContext, ...], ...], str]:
    targets = static_cd_targets(plan)
    if targets is None:
        return (), "Directory changes cannot be statically verified"
    if targets and any(u.name == "git" for u in plan.units):
        return (), "Compound commands with cd and git require approval"
    # Status belongs to the preceding pipeline. && and || are left-associative.
    states = {(context.cwd, True)}
    contexts: list[tuple[ToolUseContext, ...]] = []
    index = 0
    while index < len(plan.units):
        end = index + 1
        while end < len(plan.units) and plan.units[end].connector in {
            Connector.PIPE,
            Connector.PIPE_AMP,
        }:
            end += 1
        connector = plan.units[index].connector
        runs = {
            (cwd, ok)
            for cwd, ok in states
            if connector not in {Connector.AND, Connector.OR}
            or (connector is Connector.AND and ok)
            or (connector is Connector.OR and not ok)
        }
        following = states - runs
        candidates = tuple(
            replace(context, cwd=cwd) for cwd in sorted({c for c, _ in runs})
        )
        # Do not infer command success from executable names; every ordinary
        # command may fail, and both paths must pass permissions before spawn.
        for cwd, _ in runs:
            destination = cwd
            if index in targets:
                target = targets[index]
                if context.environment.get("CDPATH") and not (
                    target.startswith(("/", "./", "../")) or target in (".", "..")
                ):
                    return (), "cd target depends on CDPATH and requires approval"
                destination = expand_path(target, cwd)
                if not path_in_allowed_working_path(destination, context):
                    return (
                        (),
                        f"cd target '{target}' is outside the working directories",
                    )
                if not os.path.isdir(destination):
                    return (), f"cd target '{target}' is not an existing directory"
            following.update(((destination, True), (cwd, False)))
        contexts.extend([candidates] * (end - index))
        states = following
        if len(states) > 64:
            return (), "Too many possible working directories to verify"
        index = end
    return tuple(contexts), ""
