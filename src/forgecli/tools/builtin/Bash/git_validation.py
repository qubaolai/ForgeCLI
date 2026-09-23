"""Git subcommands whose positional arguments can turn a query into a write."""

from __future__ import annotations


def git_query_is_read_only(args: tuple[str, ...]) -> bool:
    if not args:
        return False
    command, *rest = args
    if command in {"branch", "tag"}:
        list_mode = False
        flags = {
            "-l",
            "--list",
            "-a",
            "--all",
            "-r",
            "--remotes",
            "-v",
            "-vv",
            "--verbose",
            "--no-color",
            "--ignore-case",
            "-i",
            "--column",
            "--no-column",
        }
        if command == "tag":
            flags -= {"-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose"}
        values = {
            "--contains",
            "--no-contains",
            "--merged",
            "--no-merged",
            "--points-at",
            "--sort",
            "--format",
            "--color",
        }
        index = 0
        positional = False
        while index < len(rest):
            arg = rest[index]
            if arg == "--":
                positional = True
                index += 1
                continue
            if not positional and arg in flags:
                if arg in {"-l", "--list"}:
                    list_mode = True
                index += 1
                continue
            if not positional and arg.split("=", 1)[0] in values:
                if "=" not in arg:
                    index += 1
                    if index >= len(rest) or rest[index].startswith("-"):
                        return False
                index += 1
                continue
            if not list_mode or (not positional and arg.startswith("-")):
                return False
            index += 1
        return True
    if command == "config":
        options = {
            "--global",
            "--local",
            "--system",
            "--show-origin",
            "--show-scope",
            "--includes",
            "--no-includes",
            "--null",
            "-z",
        }
        rest = [arg for arg in rest if arg not in options]
        if rest == ["--list"] or rest == ["-l"]:
            return True
        return (
            len(rest) == 2
            and rest[0] in {"--get", "--get-all", "--get-regexp"}
            and not rest[1].startswith("-")
        )
    if command == "remote":
        return not rest or rest in (["-v"], ["--verbose"])
    if command == "stash":
        return (
            bool(rest)
            and rest[0] in {"list", "show"}
            and not any(
                arg.startswith(("--output", "--ext-diff", "--textconv")) for arg in rest
            )
        )
    # Output files and external diff helpers are never read-only capabilities.
    if any(arg.startswith(("--output", "--ext-diff", "--textconv")) for arg in rest):
        return False
    if (
        command == "reflog"
        and rest
        and rest[0] in {"expire", "delete", "drop", "write"}
    ):
        return False
    return command in {
        "status",
        "log",
        "diff",
        "show",
        "rev-parse",
        "ls-files",
        "ls-tree",
        "blame",
        "describe",
        "shortlog",
        "reflog",
        "cat-file",
        "rev-list",
        "name-rev",
        "grep",
        "help",
        "version",
    }
