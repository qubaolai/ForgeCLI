"""执行环境的净化规则 (ADR-0014 §4.2).

普通 Allow 不得继承未约束的登录 Shell 环境. 原因很具体: 一个能写工作区的 Agent 只要
往 `.envrc`, `PYTHONPATH` 或 `node_modules/.bin` 里放点东西, 下一条"看起来安全"的命令
就会执行它写进去的代码. 授权时看到的 `python3` 与真正跑起来的 `python3` 必须是同一个.

所以这里做三件事:

1. `PATH` 由 Forge 按平台和显式工具链配置构造, **不含 `.`**, 不含工作区, 不含临时目录.
2. 能注入代码, 改变命令查找或加载项目配置的环境变量一律清除, 保留项用显式 allowlist.
3. Shell 以非交互, 非登录, 不加载 profile/rc 的方式启动.

纯函数, 不读 os.environ, 不认识当前平台 —— 那些是探测器 (infrastructure) 的事.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

__all__ = [
    "DEFAULT_ENV_ALLOWLIST",
    "ENVIRONMENT_SANITIZATION_VERSION",
    "INJECTION_VARIABLE_PREFIXES",
    "INJECTION_VARIABLES",
    "ShellLaunch",
    "sanitize_environment",
]

# 净化规则版本. 规则变化会改变 effective_environment_hash, 使旧授权与缓存失效.
ENVIRONMENT_SANITIZATION_VERSION = "2"

# 能改变"这条命令实际执行什么"的变量. 清除而不是保留, 因为它们的危害不取决于取值内容,
# 而取决于它们存在本身.
# ADR-0040 C 类表: 它**不是**主机制. 主机制是下面那份正向 allowlist ——
# 只有列进 DEFAULT_ENV_ALLOWLIST 的变量才会进子进程, 其余一律不传.
# 表外默认: 没被这份 denylist 点名的变量, 只要不在 allowlist 里同样进不去.
# 漏一项的后果: 无 —— 这份清单存在只是为了让"为什么清掉它"有个可读的理由.
INJECTION_VARIABLES: tuple[str, ...] = (
    # POSIX shell 启动与命令查找
    "ENV",
    "BASH_ENV",
    "BASH_FUNC_",
    "ZDOTDIR",
    "PROMPT_COMMAND",
    "CDPATH",
    "IFS",
    "SHELLOPTS",
    "BASHOPTS",
    # 动态链接
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "LD_AUDIT",
    # 解释器
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "PYTHONHOME",
    "PYTHONEXECUTABLE",
    "NODE_OPTIONS",
    "NODE_PATH",
    "PERL5LIB",
    "PERL5OPT",
    "RUBYOPT",
    "RUBYLIB",
    "GEM_PATH",
    "CLASSPATH",
    "JAVA_TOOL_OPTIONS",
    # 版本管理器与包管理器的注入点
    "PIP_INDEX_URL",
    "PIP_EXTRA_INDEX_URL",
    "NPM_CONFIG_PREFIX",
    "NPM_CONFIG_REGISTRY",
    # Windows PowerShell 模块与 profile
    "PSMODULEPATH",
    "POWERSHELL_TELEMETRY_OPTOUT",
)

# 前缀匹配的清除项. 与 INJECTION_VARIABLES 同一条: ADR-0040 C 类,
# 正向 allowlist 才是主机制.
INJECTION_VARIABLE_PREFIXES: tuple[str, ...] = ("DYLD_", "BASH_FUNC_")

# 保留项白名单: 不影响命令查找与代码加载, 但缺了会让大量工具行为异常.
# 这是**封闭策略**, 不是开放穷举: 它是"授予集合" —— 只有列进来的变量才会进子进程.
# 加一项是明确的放宽决定, 漏一项只会让某个工具行为异常, 不会开洞. 与上面那份
# INJECTION_VARIABLES 的方向正好相反, 两者不要混为一谈 (ADR-0040 §8.4).
DEFAULT_ENV_ALLOWLIST: tuple[str, ...] = (
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOGNAME",
    "SHELL",
    "SYSTEMROOT",
    "TEMP",
    "TERM",
    "TMP",
    "TMPDIR",
    "TZ",
    "USER",
    "USERPROFILE",
)


@dataclass(frozen=True)
class ShellLaunch:
    """Shell 的非交互启动方式. 进 execution_profile, 变了旧授权就失效."""

    program: str
    args: tuple[str, ...]
    kind: str

    @property
    def descriptor(self) -> str:
        return " ".join((self.program, *self.args))

    @property
    def dialect(self) -> str:
        """安全解析使用的三种方言；启动程序别名只在这里归一。"""
        normalized = self.kind.casefold()
        if normalized in {"posix", "sh", "bash", "zsh", "dash", "ksh"}:
            return "posix"
        if normalized == "cmd":
            return "cmd"
        if normalized in {"powershell", "pwsh"}:
            return "powershell"
        raise ValueError(f"不支持的 Shell 方言: {self.kind}")


def sanitize_environment(
    raw: Mapping[str, str],
    *,
    trusted_path: Sequence[str],
    allowlist: Sequence[str] = DEFAULT_ENV_ALLOWLIST,
    controlled: Mapping[str, str] | None = None,
    path_separator: str = ":",
) -> dict[str, str]:
    """按 allowlist 过滤环境, 并把 PATH 换成 Forge 构造的受控 PATH.

    allowlist 之外的变量一律不带入, 因此新出现的注入向量默认被挡住 —— 用黑名单反过来
    做, 每出一个新变量都要等有人想起来补一条.
    """
    allowed = {name.upper() for name in allowlist}
    result = {
        name: value
        for name, value in raw.items()
        if name.upper() in allowed and not _is_injection(name)
    }
    if path_separator not in (":", ";"):
        raise ValueError("PATH 分隔符只能是 ':' 或 ';'")
    result["PATH"] = path_separator.join(trusted_path)
    # 这些值来自冻结的 ExecutionProfile，不来自宿主环境。允许它们覆盖透传值，
    # 用于关闭 git/pip/npm 等工具的隐式用户级配置入口。
    result.update(dict(controlled or {}))
    return result


def _is_injection(name: str) -> bool:
    upper = name.upper()
    if upper in {item.upper() for item in INJECTION_VARIABLES}:
        return True
    return any(upper.startswith(prefix) for prefix in INJECTION_VARIABLE_PREFIXES)
