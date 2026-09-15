"""执行环境的构成规则.

判据只有一条:

    Agent 环境 = 开发者环境 - Agent 可写的部分

**减法, 不是加法.** 早先这里是加法 —— 一份十四个名字的 allowlist 加一份写死的 PATH
候选目录, 其余一律不带入. 那个方向的问题不是"哪一条漏了", 而是它按定义补不完: 每加
一门语言, 每换一个平台, 差集就长一截, 而漏掉的部分不会报错, 只会让同一条命令在 Forge
里跑出与开发者终端不同的结果. 实测过的后果: `mvn` 报不存在 (它装在候选目录之外),
`java` 解析到与开发者终端不同的版本, Windows 上 `PATHEXT` 与 `COMSPEC` 从来没进过表.

等价性是主要需求: Agent 跑 `npm test` 得到的结论必须和开发者自己跑一致, 否则它不是
少了个功能, 而是会拿一个不是你发布环境的环境报"测试通过".

减法的对象是可以算出来的, 不用猜:

1. **PATH** 继承启动 shell, 只减去两类条目 —— 相对路径, 以及落在工作区里的目录.
   工作区是唯一在所有隔离档下都确定属于 Agent 可写的地方; 其余目录有围栏时写不进,
   没围栏时每条命令都要人点头, 而且可执行文件身份那一闸还会拒绝让 Agent 可写位置的
   文件继承同名系统工具的授权.
2. **其余变量** 按 `EnvironmentInheritance` 三档取. 默认全继承 —— 子进程改不了父进程
   的环境, 所以这些变量的写入方只有启动 forge 的那个人.
3. **受控变量** 由 Forge 写死并覆盖继承值, 用来关掉会改变命令语义的隐式用户级配置.
4. **Shell** 以非交互, 非登录, 不加载 profile/rc 的方式启动 —— 这一条不受继承档影响:
   rc 一加载, Agent 写进工作区的 `.envrc` 之类就生效了, 那正是要挡的自指.

纯函数, 不读 os.environ, 不认识当前平台 —— 那些是探测器 (infrastructure) 的事.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "CORE_ENV_NAMES",
    "ENVIRONMENT_SANITIZATION_VERSION",
    "EnvironmentInheritance",
    "ShellLaunch",
    "sanitize_environment",
]

# 净化规则版本. 规则变化会改变 effective_environment_hash, 使旧授权与缓存失效.
ENVIRONMENT_SANITIZATION_VERSION = "3"

# `core` 档要保留的变量名. 它不再是主机制 —— 默认档是全继承, 这份表只在用户显式选择
# 收紧时生效. 判据是"不影响命令查找与代码加载, 但缺了会让大量工具行为异常".
#
# 它按定义是不完整的 (Windows 还要 PATHEXT / COMSPEC / ProgramData, 每门语言还有自己
# 的一批), 而这正是它不能当默认值的原因: 一份注定不全的表, 只适合给明确要求可复现的
# 人用, 不适合替所有人做决定.
CORE_ENV_NAMES: tuple[str, ...] = (
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


class EnvironmentInheritance(Enum):
    """从启动 shell 继承多少环境.

    默认是 ALL. 理由是两条需求分量不同: 等价性不成立, Agent 会直接产出错误结论 (拿一个
    不是你发布环境的环境报"测试通过"); 可复现性不成立, 只是同一个任务在两台机器上表现
    不同 —— 需要人注意, 但不会骗人.

    三档都进 execution_profile_hash, 所以换档会让既有授权与学习规则失效并重新裁决.
    """

    #: 全继承. PATH 仍然按 Agent 可写性筛过, 见模块说明第 1 条.
    ALL = "all"
    #: 只留 CORE_ENV_NAMES. 给明确要求跨机器可复现的场景.
    CORE = "core"
    #: 一个都不继承, 只剩 PATH 与受控变量. 给最严格的复现场景.
    NONE = "none"


@dataclass(frozen=True)
class ShellLaunch:
    """Shell 的非交互启动方式. 进 execution_profile, 变了旧授权就失效."""

    program: str
    args: tuple[str, ...]
    kind: str

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
    inheritance: EnvironmentInheritance = EnvironmentInheritance.ALL,
    controlled: Mapping[str, str] | None = None,
    path_separator: str = ":",
) -> dict[str, str]:
    """按继承档取宿主环境, 再把 PATH 换成已经筛过的受控 PATH.

    PATH 无论哪一档都由 `trusted_path` 决定, 不从 raw 里取: 筛除工作区条目这件事发生在
    画像构造期 (探测器那一侧, 它才知道工作区在哪), 这里只负责把结论装配上去.
    """
    if inheritance is EnvironmentInheritance.NONE:
        result: dict[str, str] = {}
    elif inheritance is EnvironmentInheritance.CORE:
        core = {name.upper() for name in CORE_ENV_NAMES}
        result = {name: value for name, value in raw.items() if name.upper() in core}
    else:
        result = dict(raw)
    if path_separator not in (":", ";"):
        raise ValueError("PATH 分隔符只能是 ':' 或 ';'")
    result["PATH"] = path_separator.join(trusted_path)
    # 这些值来自冻结的 ExecutionProfile, 不来自宿主环境. 允许它们覆盖继承值,
    # 用于关闭 git/pip/npm 等工具的隐式用户级配置入口.
    result.update(dict(controlled or {}))
    return result
