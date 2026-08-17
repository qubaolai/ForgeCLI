"""Hard Deny 底线与预扫描 (ADR-0013 §4 / §8).

Hard Deny 是**任何模式, 任何用户确认, 任何分类器结论都不能覆盖**的底线. 它不是"风险很
高"的同义词, 而是"这件事 Forge 不做, 请你自己去做".

两层结构, 缺一不可:

- ``inspect_command``: 基于解析出的 CommandPlan 做结构化判定. 解析成功时用它, 精度高.
- ``prefilter_raw``: **不依赖完整 AST**, 直接在原始命令串上找已知硬性模式. 它只在解析
  失败或不完整时使用 —— 解析不出来的命令恰恰最可疑, 那时至少还有这一层.

两者分工而不是叠加是有原因的: 正则会把 `git commit -m "别用 sudo"` 里引号内的 sudo 也
当成提权. 解析成功时这种误判没必要付, 解析失败时这种误判值得付.

预扫描未命中只表示"没发现已知硬性模式", 不表示安全. 能解析且可由人类明确批准的外部
高影响操作 (git push --force, npm publish) 走 Mandatory Ask, **不**在这里一概 Deny.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from forgecli.domain.security.decision import RiskFact
from forgecli.domain.security.shell.command_plan import (
    CommandPlan,
    CommandUnit,
    Connector,
)
from forgecli.domain.security.shell.wrappers import PRIVILEGE_ESCALATORS
from forgecli.domain.security.vocabulary import DecisionReason

__all__ = ["HardDenyHit", "inspect_command", "prefilter_raw"]


@dataclass(frozen=True)
class HardDenyHit:
    reason: DecisionReason
    fact: RiskFact


# 块设备与卷操作: 直接写裸设备或重建文件系统, 没有任何恢复手段.
_BLOCK_DEVICE = re.compile(
    r"(?:^|[\s|;&])(?:mkfs(?:\.\w+)?|fdisk|parted|diskutil\s+(?:erase|partition)"
    r"|dd\b[^|;&]*\bof=/dev/|shred\b|blkdiscard|wipefs)",
    re.IGNORECASE,
)

# 递归删除根或家目录. 匹配的是"目标是根", 不是"命令叫 rm".
_ROOT_WIPE = re.compile(
    r"\brm\b[^|;&]*\s-[a-zA-Z]*[rR][a-zA-Z]*[fF]?[a-zA-Z]*\s+"
    r"(?:/|/\*|~|~/|\$HOME|/\s*$)(?:\s|$)"
)

# 下载后直接执行: 网络内容喂给解释器.
_DOWNLOAD_EXEC = re.compile(
    r"(?:curl|wget|iwr|Invoke-WebRequest)\b[^|]*\|\s*(?:sudo\s+)?"
    r"(?:ba|z|k|d)?sh\b|(?:curl|wget)\b[^|]*\|\s*(?:python|perl|ruby|node)\b",
    re.IGNORECASE,
)

# 反弹 shell 的常见形态.
_REVERSE_SHELL = re.compile(
    r"(?:nc|ncat|netcat)\b[^|;&]*\s-[a-zA-Z]*e[a-zA-Z]*\s"
    r"|bash\s+-i\s*>&\s*/dev/tcp/"
    r"|/dev/tcp/\d|socat\b[^|;&]*exec:",
    re.IGNORECASE,
)

# 关键进程破坏.
_PROCESS_KILL = re.compile(
    r"\bkill\s+-9\s+1\b|\bkillall5\b|:\(\)\s*\{\s*:\|\s*:&\s*\}\s*;\s*:",
)

# 凭证外泄: 把密钥内容送出去.
_CREDENTIAL_EXFIL = re.compile(
    r"(?:cat|type|Get-Content)\b[^|;&]*"
    r"(?:\.ssh/id_|\.aws/credentials|\.netrc|\.npmrc|\.pypirc|id_rsa|id_ed25519)"
    r"[^|;&]*\|\s*(?:curl|wget|nc|ncat|mail|Invoke-WebRequest)",
    re.IGNORECASE,
)

# 防止提权
_PRIVILEGE = re.compile(
    r"(?:^|[\s|;&])(?:sudo|doas|pkexec|runas)\b|(?:^|[\s|;&])su\s",
    re.IGNORECASE,
)

_PREFILTER_RULES: tuple[tuple[re.Pattern[str], DecisionReason, str], ...] = (
    (_BLOCK_DEVICE, DecisionReason.HARD_DENY_DESTRUCTIVE, "块设备或卷操作"),
    (_ROOT_WIPE, DecisionReason.HARD_DENY_DESTRUCTIVE, "对根或家目录的递归删除"),
    (
        _DOWNLOAD_EXEC,
        DecisionReason.HARD_DENY_REMOTE_CODE_EXECUTION,
        "把网络内容直接交给解释器执行",
    ),
    (_REVERSE_SHELL, DecisionReason.HARD_DENY_REMOTE_CODE_EXECUTION, "反弹 Shell"),
    (_PROCESS_KILL, DecisionReason.HARD_DENY_DESTRUCTIVE, "关键进程破坏"),
    (_CREDENTIAL_EXFIL, DecisionReason.HARD_DENY_CREDENTIAL_ACCESS, "凭证外泄"),
    (_PRIVILEGE, DecisionReason.HARD_DENY_PRIVILEGE_ESCALATION, "提权执行"),
)


def prefilter_raw(raw_command: str) -> HardDenyHit | None:
    """解析失败时的硬性模式预扫描.

    直接在原始串上匹配, 因此会把引号里的字面量也算上. 误报的代价是一次拒绝, 漏报的
    代价是一次不可撤销的破坏 —— 在"连命令都解析不出来"的前提下, 刻意偏向前者.
    """
    for pattern, reason, detail in _PREFILTER_RULES:
        if pattern.search(raw_command):
            return HardDenyHit(
                reason=reason, fact=RiskFact(code="hard_deny", detail=detail)
            )
    return None


def inspect_command(plan: CommandPlan) -> HardDenyHit | None:
    """基于结构化计划的 Hard Deny 判定. 解析成功时用它."""
    if plan.pipes_into_interpreter:
        return _hit(
            DecisionReason.HARD_DENY_REMOTE_CODE_EXECUTION,
            "上级输出经管道直接进入解释器",
        )
    exfil = _credential_exfiltration(plan)
    if exfil is not None:
        return exfil
    for unit in plan.units:
        hit = _inspect_unit(unit)
        if hit is not None:
            return hit
    return None


# ---- 结构化判定 ----

_DESTRUCTIVE_DEVICE_TOOLS = frozenset(
    {"mkfs", "fdisk", "parted", "shred", "blkdiscard", "wipefs", "hdparm"}
)
_NETWORK_SINKS = frozenset(
    {
        "curl",
        "wget",
        "nc",
        "ncat",
        "netcat",
        "socat",
        "mail",
        "Invoke-WebRequest",
        "iwr",
    }
)
_CREDENTIAL_MARKERS = (
    ".ssh/id_",
    "id_rsa",
    "id_ed25519",
    ".aws/credentials",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".docker/config.json",
    ".kube/config",
)
_ROOT_TARGETS = frozenset({"/", "/*", "~", "~/", "/.", "C:\\", "C:/", "C:\\*"})
_DEVICE_PREFIXES = ("/dev/", "\\\\.\\", "\\\\?\\")
_DELETERS = frozenset({"rm", "del", "rmdir", "Remove-Item"})


def _inspect_unit(unit: CommandUnit) -> HardDenyHit | None:
    # Start-Process 不在这里: 它是 PowerShell 的通用启动命令, 提权与否看参数,
    # 由规则层按参数判断, 不一刀切.
    if unit.name in PRIVILEGE_ESCALATORS and unit.name != "Start-Process":
        return _hit(
            DecisionReason.HARD_DENY_PRIVILEGE_ESCALATION,
            f"提权执行: {unit.executable}",
        )
    if unit.name in _DELETERS and _targets_root(unit):
        return _hit(DecisionReason.HARD_DENY_DESTRUCTIVE, f"删除根级目标: {unit.raw}")
    if _is_device_tool(unit.name):
        return _hit(
            DecisionReason.HARD_DENY_DESTRUCTIVE, f"块设备或卷操作: {unit.executable}"
        )
    if unit.name == "dd" and any(arg.startswith("of=/dev/") for arg in unit.argv):
        return _hit(DecisionReason.HARD_DENY_DESTRUCTIVE, f"dd 写入裸设备: {unit.raw}")
    if unit.name == "diskutil" and any(
        arg in ("erase", "eraseDisk", "eraseVolume", "partitionDisk")
        for arg in unit.argv
    ):
        return _hit(DecisionReason.HARD_DENY_DESTRUCTIVE, f"卷销毁: {unit.raw}")
    if _writes_device(unit):
        return _hit(DecisionReason.HARD_DENY_DESTRUCTIVE, f"写入设备路径: {unit.raw}")
    if _is_reverse_shell(unit):
        return _hit(
            DecisionReason.HARD_DENY_REMOTE_CODE_EXECUTION, f"反弹 Shell: {unit.raw}"
        )
    if _kills_init(unit):
        return _hit(DecisionReason.HARD_DENY_DESTRUCTIVE, f"关键进程破坏: {unit.raw}")
    return None


def _credential_exfiltration(plan: CommandPlan) -> HardDenyHit | None:
    """读凭证 -> 管道 -> 网络工具. 判据是数据流形状, 不是命令名."""
    for previous, unit in zip(plan.units, plan.units[1:], strict=False):
        if unit.connector not in (Connector.PIPE, Connector.PIPE_AMP):
            continue
        reads_credentials = any(
            marker in arg for arg in previous.argv for marker in _CREDENTIAL_MARKERS
        )
        if reads_credentials and unit.name in _NETWORK_SINKS:
            return _hit(
                DecisionReason.HARD_DENY_CREDENTIAL_ACCESS,
                f"凭证内容被送往 {unit.executable}",
            )
    return None


def _targets_root(unit: CommandUnit) -> bool:
    targets = [arg for arg in unit.argv if not arg.startswith("-")]
    return any(target in _ROOT_TARGETS for target in targets)


def _writes_device(unit: CommandUnit) -> bool:
    return any(
        target.startswith(prefix)
        for target in unit.write_targets
        for prefix in _DEVICE_PREFIXES
    )


def _is_reverse_shell(unit: CommandUnit) -> bool:
    if unit.name in ("nc", "ncat", "netcat") and any(
        arg.startswith("-") and "e" in arg for arg in unit.argv
    ):
        return True
    if unit.name == "socat" and any(
        arg.lower().startswith("exec:") for arg in unit.argv
    ):
        return True
    return any("/dev/tcp/" in target for target in unit.write_targets)


def _kills_init(unit: CommandUnit) -> bool:
    if unit.name == "killall5":
        return True
    return unit.name == "kill" and "1" in unit.argv and "-9" in unit.argv


def _is_device_tool(name: str) -> bool:
    """块设备工具. `mkfs` 是一个**家族**: mkfs.ext4, mkfs.xfs, mkfs.vfat 都是它.

    按家族前缀判定而不是把点号后面截掉, 是因为截断规则会顺手把无关命令也归错类.
    """
    return name in _DESTRUCTIVE_DEVICE_TOOLS or name.startswith("mkfs.")


def _hit(reason: DecisionReason, detail: str) -> HardDenyHit:
    return HardDenyHit(reason=reason, fact=RiskFact(code="hard_deny", detail=detail))
