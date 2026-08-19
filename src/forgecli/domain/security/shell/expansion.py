"""受控目标展开: 把命令里的目标变成封闭的绝对路径集合 (ADR-0013 §6.2).

规则很硬: **直接修改真实工作区的普通 ALLOW 只适用于 STATIC 或 FORGE_EXPANDED.**

    rm ./*                      简单 glob, Forge 可以用冻结视图展开 -> FORGE_EXPANDED
    rm -rf "$DIR"               变量目标, 运行期才确定 -> DYNAMIC
    find . -name '*.tmp' -exec rm {} +   运行期产生 -> DYNAMIC
    printf ... | xargs rm       管道输入决定 -> DYNAMIC

展开必须用已经绑定的 cwd 与文件系统视图, 结果规范化为完整绝对路径. 展不开的就老实说
展不开 —— 猜一个目标集合比不猜更危险.

展开结果**只用于裁决与审批展示**, 不回写成执行用的 argv: 执行仍然把原始命令串交给
非交互 shell, 所以 glob 在执行时会被 shell 再展开一次. 两次展开之间的窗口由恢复点兜住,
不由这里假装消除 (ADR-0021).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath

from forgecli.domain.security.shell.arguments import classify_arguments
from forgecli.domain.security.shell.command_plan import CommandPlan, CommandUnit
from forgecli.domain.security.shell.effects import EffectKind, effect_kind_of
from forgecli.domain.tool.plan import TargetResolution

__all__ = ["ExpansionResult", "expand_home", "expand_targets"]

# 目标由运行期决定的间接执行命令.
_DYNAMIC_PRODUCERS = frozenset({"xargs", "find", "eval", "parallel"})

_GLOB_CHARS = ("*", "?", "[")


@dataclass(frozen=True)
class ExpansionResult:
    resolution: TargetResolution
    targets: tuple[str, ...]
    reasons: tuple[str, ...] = ()

    @property
    def closed(self) -> bool:
        return self.resolution.closed


def expand_targets(
    plan: CommandPlan,
    *,
    cwd: str,
    glob: Callable[[str], tuple[str, ...]],
    home: str = "",
) -> ExpansionResult:
    """尝试封闭整条命令的写 / 删 / 移目标集合.

    glob 由调用方注入 (通常是冻结的 FileSystemView.expand_glob): 展开是纯函数, 但它需要
    一份**确定的**文件系统视图, 而视图属于执行上下文, 不属于领域.
    """
    reasons: list[str] = []
    targets: list[str] = []
    dynamic = False

    for unit in plan.units:
        if unit.name in _DYNAMIC_PRODUCERS:
            dynamic = True
            reasons.append(f"{unit.name} 的目标由运行期产生")
            continue
        if effect_kind_of(unit) is EffectKind.UNPROVEN:
            # 不认识这个命令. 位置参数照样收 (多报无害), 但集合**不能算封闭** ——
            # 封闭的语义是"这就是全部目标", 而这里连它写不写都不知道.
            dynamic = True
            reasons.append(f"{unit.name} 的影响范围无法推导")
        if unit.opaque_reasons:
            dynamic = True
            reasons.extend(unit.opaque_reasons)
        for candidate in _candidate_targets(unit):
            resolved = _resolve(candidate, cwd, home)
            if _has_glob(resolved):
                matched = glob(resolved)
                if not matched:
                    dynamic = True
                    reasons.append(f"glob 无法展开: {candidate}")
                    continue
                targets.extend(matched)
                continue
            if _looks_unresolved(resolved):
                dynamic = True
                reasons.append(f"目标含未展开引用: {candidate}")
                continue
            targets.append(resolved)

    unique = tuple(sorted(dict.fromkeys(targets)))
    if dynamic:
        return ExpansionResult(
            resolution=TargetResolution.DYNAMIC,
            targets=unique,
            reasons=tuple(dict.fromkeys(reasons)),
        )
    # 封闭成功一律记 FORGE_EXPANDED, 即使目标写得再显式, 甚至一个目标都没有.
    #
    # STATIC 有专门的含义: **工具自己**用结构化入参证明了目标集合 (fs.write_patch 的
    # path 参数). 分析器是从一条不透明的命令串里推出来的, 冒用 STATIC 等于把推导结果
    # 伪装成工具的原始声明 —— validate_narrowing 也正是这么判的.
    return ExpansionResult(resolution=TargetResolution.FORGE_EXPANDED, targets=unique)


def _candidate_targets(unit: CommandUnit) -> tuple[str, ...]:
    """该单元可能改写的目标: 路径参数 + 输出重定向目标.

    这里不区分"这个命令到底会不会写": 判断 `sed -i` 写不写文件属于规则层的事实分析,
    展开层只负责把候选目标变成可判定的绝对路径.

    哪些参数算路径交给 classify_arguments —— 把 `sed` 的脚本或 `grep` 的搜索词当成候选
    目标, 展开层会拿它去 glob, 去比对受保护路径, 最后在审批框里当成一个文件展示出来.
    """
    return (*classify_arguments(unit).paths, *unit.write_targets)


def _resolve(candidate: str, cwd: str, home: str = "") -> str:
    """把候选目标变成绝对路径.

    `~` 与 `$HOME` 用**冻结环境快照**里的 HOME 展开: 它是已经绑定的事实, 属于
    ADR-0013 §6.2 允许的受控展开. 不展开的后果更糟 —— `cat ~/.ssh/id_rsa` 会因为
    "看起来不是路径"而绕过受保护路径检查.
    """
    candidate = expand_home(candidate, home)
    if candidate.startswith("~"):
        return candidate  # 没有 HOME 可用: 交给 _looks_unresolved 判定
    path = PurePosixPath(candidate)
    if path.is_absolute():
        return str(path)
    return str(PurePosixPath(cwd) / path) if cwd else candidate


def expand_home(candidate: str, home: str) -> str:
    """用冻结环境快照里的 HOME 展开 `~` 与 `$HOME`."""
    if not home:
        return candidate
    if candidate == "~":
        return home
    if candidate.startswith("~/"):
        return f"{home.rstrip('/')}/{candidate[2:]}"
    for marker in ("$HOME", "${HOME}"):
        if candidate.startswith(marker):
            return f"{home.rstrip('/')}{candidate[len(marker) :]}"
    return candidate


def _has_glob(candidate: str) -> bool:
    return any(char in candidate for char in _GLOB_CHARS)


def _looks_unresolved(candidate: str) -> bool:
    return (
        "$" in candidate
        or "%" in candidate
        or candidate.startswith("~")
        or "\x00sub" in candidate
    )
