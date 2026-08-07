"""执行环境的净化规则(ADR-0014 §4.2)

普通Allow不得继承未约束的登录shell环境 原因: 一个能写工作区的 agent 
只要往 .envrc, PYTHONPATH 或 node_modules/.bin 里面放点东西, 下一条看起来安全的命令
就会执行它写进去的代码. 授权时看到的python3 与 真正跑起来的python3 必须是同一个东西

所以这里做三件事:
1. PATH 由 Forge按平台和现实工具链配置构造 不含. 不含工作区 不含临时目录
2. 能注入代码, 改变命令查找或者加载项目配置的环境变量一律清楚, 保留项用显示的 allowlist
3. shell 以非交互 非登录 不加载 profile/rc 的方式启动
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tomlkit import value

__all__ = [
    "DEFAULT_ENV_ALLOWLIST",
    "ENVIRONMENT_SANITIZATION_VERSION",
    "INJECTION_VARIABLE_PREFIXES",
    "INJECTION_VARIABLES",
    "ShellLaunch",
    "sanitize_environment",
    "sanitized_names",
]

# 净化规则版本. 规则变化会改变 effective_environment_hash, 使旧授权与缓存失效.
ENVIRONMENT_SANITIZATION_VERSION = "1"

# 能改变"这条命令实际执行什么"的变量. 清除而不是保留, 因为它们的危害不取决于取值内容,
# 而取决于它们存在本身.
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

# 前缀匹配的清除项 (macOS 的 DYLD_* 一族, Bash 导出函数).
INJECTION_VARIABLE_PREFIXES: tuple[str, ...] = ("DYLD_", "BASH_FUNC_")

# 保留项白名单: 不影响命令查找与代码加载, 但缺了会让大量工具行为异常.
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
    """shell的非交互式启动, 进execution_profile, 变了方式 所有旧的授权都失效"""

    program: str
    args: tuple[str, ...]
    kind: str

    @property
    def descriptor(self) -> str:
        return " ".join((self.program, *self.args))


def sanitize_environment(
    raw: Mapping[str, str],
    *,
    trusted_path: Sequence[str],
    allowlist: Sequence[str] = DEFAULT_ENV_ALLOWLIST,
) -> dict[str, str]:
    """按 allowlist 过滤环境, 并把 PATh 换成 forge 构造的受控的 PATH
    
    allowlist 之外的变量一律不带入, 因此新出现的注入默认被挡住, 而如果用黑名单反过来做
    每出一个新的变量就要人为补一条
    """
    allowed = {name.upper() for name in allowlist}
    result = {
        name: value
        for name, value in raw.items()
        if name.upper() in allowed and not _is_injection(name)
    }
    result["PATH"] = _join_path(trusted_path)
    return result

def sanitized_names(raw: Mapping[str, str]) -> tuple[str, ...]:
    """列出被清除的注入类变量名, 供审计展示 (只记名字, 不记取值)."""
    return tuple(sorted(name for name in raw if _is_injection(name)))

def _is_injection(name: str) -> bool:
    upper = name.upper()
    if upper in {item.upper() for item in INJECTION_VARIABLES}:
        return True
    return any(upper.startswith(prefix) for prefix in INJECTION_VARIABLE_PREFIXES)

def _join_path(entries: Sequence[str]) -> str:
    # 分隔符按 POSIX 与 Windows 区分由探测器决定; 这里保持传入顺序不做重排.
    separator = ";" if any("\\" in entry for entry in entries) else ":"
    return separator.join(entries)