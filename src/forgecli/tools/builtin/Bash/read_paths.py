"""Extract file operands without treating search patterns as filenames."""

from __future__ import annotations

from forgecli.security.shell.command_plan import CommandUnit
from forgecli.tools.builtin.Bash.sed_validation import sed_read_paths

_FILES = {
    "ls",
    "cat",
    "head",
    "tail",
    "wc",
    "stat",
    "file",
    "tree",
    "du",
    "nl",
    "strings",
    "hexdump",
    "od",
    "less",
    "more",
    "sort",
    "uniq",
    "diff",
    "cmp",
    "md5sum",
    "sha1sum",
    "sha256sum",
    "shasum",
    "readlink",
    "realpath",
}
_VALUES = {
    "ls": {"--ignore", "--hide", "--block-size", "--sort", "--time-style", "-I"},
    "head": {"-n", "-c", "--lines", "--bytes"},
    "tail": {"-n", "-c", "--lines", "--bytes", "--pid", "-s"},
    "sort": {"-k", "-t", "-S", "--key", "--field-separator", "--buffer-size"},
    "stat": {"-f", "-c", "--format", "--printf"},
    "shasum": {"-a", "--algorithm"},
}
_SEARCH_VALUES = {
    "-A",
    "-B",
    "-C",
    "-m",
    "--after-context",
    "--before-context",
    "--context",
    "--max-count",
    "--include",
    "--exclude",
    "--exclude-dir",
    "-g",
    "--glob",
    "--iglob",
    "-t",
    "--type",
    "-T",
    "--type-not",
}


def read_operands(unit: CommandUnit) -> tuple[str, ...]:
    if unit.name == "git" and unit.argv[:1] == ("diff",) and "--no-index" in unit.argv:
        return tuple(arg for arg in unit.argv[1:] if not arg.startswith("-"))
    if unit.name == "sed":
        return sed_read_paths(unit) or ()
    if unit.name == "find":
        paths = []
        for arg in unit.argv:
            if arg.startswith("-") or arg in {"!", "("}:
                break
            paths.append(arg)
        return tuple(paths) or (".",)
    search = unit.name in {"grep", "egrep", "fgrep", "rg"}
    if not search and unit.name not in _FILES:
        return ()
    pattern_seen = not search or "--files" in unit.argv
    paths = []
    args = iter(unit.argv)
    options = True
    for arg in args:
        if options and arg == "--":
            options = False
            continue
        if options and search and arg in {"-e", "--regexp", "-f", "--file"}:
            value = next(args, "")
            if arg in {"-f", "--file"}:
                paths.append(value)
            pattern_seen = True
            continue
        if options and search and arg.startswith(("--regexp=", "--file=")):
            if arg.startswith("--file="):
                paths.append(arg.split("=", 1)[1])
            pattern_seen = True
            continue
        if options and search and arg.startswith("-e") and len(arg) > 2:
            pattern_seen = True
            continue
        values = _SEARCH_VALUES if search else _VALUES.get(unit.name, set())
        if options and arg in values:
            next(args, None)
            continue
        if options and arg.startswith("-") and arg != "-":
            continue
        if not pattern_seen:
            pattern_seen = True
        elif arg != "-":
            paths.append(arg)
    return tuple(paths)
