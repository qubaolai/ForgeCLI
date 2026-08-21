"""从 CommandPlan 推导 PlanEffects (ADR-0013 §6.2, ADR-0028).

从 ShellCapabilityAnalyzer 分出来的一段. 它只回答一个问题: **这条命令会碰哪些路径,
用哪种方式碰.** 不判断允不允许, 也不认识 mode 与审批.

分出来的理由不是文件太长, 是这段代码的错法和别处不一样: 它错了会**凭空造出目标**,
而一份掺假的目标清单比没有清单更糟 —— 用户学会忽略那份清单之后, 真正危险的那一条也
跟着被忽略了.
"""

from __future__ import annotations

from collections.abc import Callable
from fnmatch import fnmatch

from forgecli.domain.security.shell.arguments import classify_arguments
from forgecli.domain.security.shell.command_plan import (
    CommandPlan,
    CommandUnit,
    UnitOrigin,
)
from forgecli.domain.security.shell.effects import (
    EffectKind,
    effect_kind_of,
    proven_read_only_command,
)
from forgecli.domain.security.shell.expansion import expand_home
from forgecli.domain.tool.plan import MovePair, PlanEffects

__all__ = [
    "NETWORK_TOOLS",
    "effects_of",
    "irreversible_detail",
    "may_write",
    "mentions_irreversible",
    "proven_read_only",
    "unproven_units",
    "unresolved_targets",
]

# 会产生网络访问的命令. 判据仍是命令语义而不是名字白名单: 命中只是"声明 NETWORK_ACCESS
# 这个能力", 允不允许由规则层按目标裁决.
NETWORK_TOOLS = frozenset(
    {
        "curl",
        "wget",
        "nc",
        "ncat",
        "netcat",
        "socat",
        "ssh",
        "scp",
        "rsync",
        "sftp",
        "ftp",
        "telnet",
        "Invoke-WebRequest",
        "iwr",
    }
)

# 不可逆外部副作用 (ADR-0013 §4.1). 命中即 Mandatory Ask, 所有模式一视同仁.
_IRREVERSIBLE: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("git", ("push",), "推送到远端"),
    ("npm", ("publish", "unpublish", "deprecate", "dist-tag"), "发布 npm 包"),
    ("yarn", ("publish",), "发布 npm 包"),
    ("pnpm", ("publish",), "发布 npm 包"),
    ("docker", ("push",), "推送镜像"),
    ("terraform", ("apply", "destroy"), "基础设施变更"),
    ("kubectl", ("apply", "delete", "drain"), "集群变更"),
    ("aws", ("deploy", "s3", "cloudformation"), "云端变更"),
    ("gh", ("release",), "发布 release"),
    ("cargo", ("publish",), "发布 crate"),
    ("twine", ("upload",), "发布 Python 包"),
)

_MAY_WRITE_KINDS = (
    EffectKind.WRITE,
    EffectKind.DELETE,
    EffectKind.MOVE,
    # 会跑任意代码的当然可能写 (`npm test` 写 coverage, `java -jar` 写什么都可以).
    EffectKind.RUNS_CODE,
    # 证明不了它不写, 就得当它会写.
    EffectKind.UNPROVEN,
)


def effects_of(
    command: CommandPlan,
    expanded: tuple[str, ...],
    home: str = "",
    *,
    resolve: Callable[[str], str],
) -> PlanEffects:
    """从命令事实推导副作用; 路径统一交给 ExecutionContext 解析.

    glob 用 expand_targets 已经展开好的结果替换, 而不是把 `*.tmp` 原样记成一个目标 ——
    审批界面要展示的是"会删掉哪两个文件", 不是一个模式串.
    """
    writes = [resolve(expand_home(t, home)) for t in command.write_targets]
    reads = [resolve(expand_home(t, home)) for t in command.read_targets]
    deletes: list[str] = []
    moves: list[str] = []
    for unit in command.units:
        # 未解析的引用 ($VAR, %VAR%) 不进目标集合: 它们只会得到一个假的绝对路径.
        # 这类命令已经由展开层标成 DYNAMIC, 由策略层要求人类确认.
        # 哪些参数是路径由 classify_arguments 判, 不再用 `not startswith("-")` 一刀切.
        # 那条判据会把 grep 的搜索词, sed 的脚本, find 的 -name 模式全当成文件, 于是
        # 审批框里的"读取 (6 项)"有一半是凭空造的.
        positional = [
            resolved
            for arg in classify_arguments(unit).paths
            if not _unresolved(expand_home(arg, home))
            for resolved in _resolve_positional(
                expand_home(arg, home), expanded, resolve=resolve
            )
        ]
        kind = effect_kind_of(unit)
        if kind is EffectKind.DELETE:
            deletes.extend(positional)
        elif kind is EffectKind.MOVE:
            moves.extend(positional)
        elif kind is EffectKind.WRITE:
            writes.extend(positional)
        else:
            # READ / RUNS_CODE / UNPROVEN 的位置参数按**读取**记. 宁可多报:
            # `cat ~/.ssh/id_rsa` 的目标必须出现在 read_paths 里, 否则受保护路径检查
            # 根本看不到它.
            #
            # 但 UNPROVEN 的读取清单**不是完整清单**, 只是"至少这些". 完整性由
            # target_resolution=DYNAMIC 与 UNPROVEN_EXECUTABLE 这条 ASK 表达, 不能
            # 靠这里的空 write_paths 冒充"它什么都没写".
            reads.extend(positional)
    return PlanEffects(
        read_paths=tuple(dict.fromkeys(reads)),
        write_paths=tuple(dict.fromkeys(writes)),
        delete_paths=tuple(dict.fromkeys(deletes)),
        move_pairs=_move_pairs(moves),
        network_targets=_network_targets(command),
        external_effects=tuple(
            dict.fromkeys(
                detail
                for unit in command.units
                if (detail := irreversible_detail(unit)) is not None
            )
        ),
        child_process=True,
        dynamic_execution=command.has_dynamic_execution,
    )


def unresolved_targets(command: CommandPlan, home: str) -> tuple[str, ...]:
    """展开之后仍然无法确定的位置参数.

    只看被判定为路径的那些 (classify_arguments): `grep "$PATTERN" .` 里的 `$PATTERN`
    是搜索词, 它没展开不影响目标集合能不能封闭, 而按未解析目标报出去会让一条本可以
    封闭的命令白白掉进 DYNAMIC.
    """
    return tuple(
        dict.fromkeys(
            arg
            for unit in command.units
            for arg in classify_arguments(unit).paths
            if _unresolved(expand_home(arg, home))
        )
    )


def unproven_units(command: CommandPlan) -> tuple[str, ...]:
    """影响范围推导不出来的可执行文件. 去重保序, 只留基名."""
    return tuple(
        dict.fromkeys(
            unit.name
            for unit in command.units
            if effect_kind_of(unit) is EffectKind.UNPROVEN
        )
    )


def may_write(command: CommandPlan) -> bool:
    """这条命令**有可能**改写东西吗.

    只有确定只读 (EffectKind.READ) 才算不写. 这个判据同时喂两处: 目标集合未封闭时要不要
    问人, 以及要不要声明写能力从而拿到恢复点.
    """
    return any(
        effect_kind_of(unit) in _MAY_WRITE_KINDS or unit.write_targets
        for unit in command.units
    )


def proven_read_only(command: CommandPlan, effects: PlanEffects) -> bool:
    """分析能否证明这条命令等价于一次读取 (ADR-0024 §决策).

    只产出事实, 不做裁决: 要不要因此免掉一次人类确认由 PolicyEngine 决定, 而它免掉的
    只有模式预算里的 EXECUTE_SHELL / SPAWN_PROCESS 一项.

    判据全部是"必须成立", 缺一即返回 False. 新增一种没见过的语法, 新增一个表外命令,
    新增一种重定向形态 —— 结果都是多问一次人, 不是少问一次.

    `may_write` 一次性覆盖了四条: 写 / 删 / 移动 / 跑任意代码的单元, 影响范围推导不出来
    的单元 (UNPROVEN), 以及任何写重定向. 它已经是"证明不了就当它会写"的口径, 正好是这里
    需要的方向.

    ADR-0028 加了一条: **每个单元都要在只读核心集里**. 这与"不写"不是同一件事 ——
    `wc`, `sort`, `xxd` 都不写, 但它们不在核心集里, 因此照旧要人类确认一次. 判据分开
    的理由是两张表的错法不同: 记账用的 READ 漏一条只是清单少一项, 而放行用的核心集
    漏一条就是少一道闸门. 详见 domain/security/shell/commands.py.
    """
    if not command.status.complete:
        return False
    if may_write(command):
        return False
    if not all(proven_read_only_command(unit) for unit in command.units):
        return False
    if command.has_dynamic_execution:
        return False
    if effects.network_targets or effects.external_effects:
        return False
    # 只认最外层单元. 命令替换, 包装器内层, 进程替换与子 Shell 都排除掉 —— 外层的
    # "只读"证明不传导给内层, 而这里要的是整条命令的证明.
    #
    # 比 ADR 列的四种 origin 更严: 多排除的 SUBSHELL 与 SCRIPT_BLOCK 只会让某些本可以
    # 放行的命令继续问人, 方向朝安全.
    return all(unit.origin is UnitOrigin.TOP_LEVEL for unit in command.units)


def irreversible_detail(unit: CommandUnit) -> str | None:
    for executable, subcommands, detail in _IRREVERSIBLE:
        if unit.name != executable:
            continue
        if any(sub in unit.argv for sub in subcommands):
            return detail
    return None


def mentions_irreversible(command: CommandPlan) -> bool:
    return any(irreversible_detail(unit) is not None for unit in command.units)


def _move_pairs(moves: list[str]) -> tuple[MovePair, ...]:
    """`mv a b c dir/` 的最后一项是目标, 其余是源."""
    if len(moves) < 2:
        return ()
    *sources, target = moves
    return tuple(MovePair(source=source, target=target) for source in sources)


def _network_targets(command: CommandPlan) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            arg
            for unit in command.units
            if unit.name in NETWORK_TOOLS
            for arg in unit.argv
            if not arg.startswith("-") and ("://" in arg or "." in arg or "@" in arg)
        )
    )


def _resolve_positional(
    arg: str, expanded: tuple[str, ...], *, resolve: Callable[[str], str]
) -> tuple[str, ...]:
    absolute = resolve(arg)
    if not any(char in absolute for char in ("*", "?", "[")):
        return (absolute,)
    matched = tuple(item for item in expanded if fnmatch(item, absolute))
    # 展不开就保留原样: 上游已经把这种情况标成 DYNAMIC, 不能悄悄当成空集合.
    return matched or (absolute,)


def _unresolved(target: str) -> bool:
    return any(marker in target for marker in ("$", "%", "~", "\x00sub"))
