"""按平台与实际安装位置生成受保护路径策略 (ADR-0014 §5.1).

不写死一张清单. Forge 的应用根目录要按**运行时实际位置**解析 —— 用 pip 装的, 用 poetry
跑的和从源码跑的位置完全不同, 写死路径等于在其中两种情况下没有保护.

同时要把两件事分开:

- ``forge_runtime_root``: 已安装的包与可执行文件, 始终受保护.
- **用户显式打开的 Forge 源码仓库**: 按普通工作区处理. editable install 会让源码目录出现
  在运行时路径里, 如果不做豁免, 用 Forge 开发 Forge 自己就会被自己拦下来.

家目录的解析不用 ``Path.home()``, 理由见 ``_home_roots``: 那个函数读 HOME 环境变量, 而
HOME 是可以被改写的, 一改保护清单就整体指向别处.
"""

from __future__ import annotations

import os
from pathlib import Path

from forgecli.domain.security.protected_paths import (
    ProtectedCategory,
    ProtectedPathPolicy,
    ProtectedRoot,
)
from forgecli.infrastructure.config.paths import config_dir, state_dir
from forgecli.infrastructure.platform_paths import windows_protected_directories

__all__ = ["build_protected_path_policy"]

# 平台敏感目录. 这是最小集合而不是完整清单: 真正的判定还要经过 realpath 归一.
#
# macOS 上 /var, /tmp 等都是指向 /private/... 的软链接, 而 classify() 看的是 realpath,
# 所以 /private 那一份必须同时列出 —— 只写 /var 的话判定时根本匹配不上.
# ADR-0040 A/C 类表: 平台系统目录. **主要边界不是它**, 是围栏 ——
# 工作区外一律不在可写集合里, 与这张表列没列到无关.
# 表外默认: 工作区外的路径本来就写不进去; 读取靠围栏的 denied_read_paths.
# 漏一项的后果: macOS 上少一条 deny-read 纵深 (Seatbelt 的读边界只能用 denylist,
# ADR-0030 实测记录第二节); Linux 由 bubblewrap 的 mount namespace 兜住.
_POSIX_SYSTEM = (
    "/System",
    "/Library",
    "/Applications",
    "/bin",
    "/sbin",
    "/usr",
    "/etc",
    "/private/etc",
    "/boot",
    "/lib",
    "/lib64",
    "/opt/homebrew/Cellar",
    # 定时任务与系统日志: 写进 cron 表等于拿到下一次的任意代码执行; 改日志是掩盖痕迹.
    "/var/spool/cron",
    "/private/var/spool/cron",
    "/var/at",
    "/var/log",
    "/private/var/log",
    # macOS 的 TCC 数据库与 sudo 时间戳都在这里. 写它可以伪造"刚刚验证过".
    "/var/db",
    "/private/var/db",
)

# 读取即外泄或直接影响运行中的服务.
# ADR-0040 C 类表: 设备与内核接口. sandbox 与 workspace grant 是上界.
# 表外默认: 不在可写集合里.
# 漏一项的后果: 同 _POSIX_SYSTEM —— 少一条纵深, 不是少一道边界.
_POSIX_DEVICES = ("/dev", "/proc", "/sys", "/run", "/var/run", "/private/var/run")

# root 的家目录. 普通用户读不到, 但以 root 运行时它是可写的 —— 而 Forge 自己的状态在
# sudo 下也会落到这里.
# ADR-0040 C 类表: root 的家目录. 由 sandbox 与 workspace grant 上界兜底.
# 表外默认: 工作区外不授权.
# 漏一项的后果: 少一条纵深.
_ROOT_HOMES = ("/root", "/var/root", "/private/var/root")

# ADR-0040 A 类表: 名字来自 Windows 环境/Known Folder API, 取值由 OS 给, 不是猜的.
# 表外默认: 取不到就退回内置默认路径 (platform_paths.windows_known_directory).
# 漏一项的后果: 少保护一个系统目录, 它仍在工作区之外.
_WINDOWS_SYSTEM_VARS = (
    "SYSTEMROOT",
    "WINDIR",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "PROGRAMDATA",
    "ALLUSERSPROFILE",
)

# 家目录所在的容器. 用它枚举"其他用户", 而不是靠当前 home 的父目录 —— 后者在 root 下
# 会变成 / 或 /var, 把一整批系统目录误标成其他用户的家目录.
# ADR-0040 B/C 类表: 用它枚举"其他用户的家目录".
# 表外默认: 工作区外的路径默认不可读写, 不因为没枚举到就变成可访问.
# 漏一项的后果: 少枚举一批其他用户目录, 它们仍在工作区之外.
_HOME_CONTAINERS = ("/home", "/Users", "/export/home", "/var/home")

# 用户目录下的凭证. 读取它们本身就是外泄, 因此 deny_read.
# ADR-0040 B 类表. **不声称完整** —— 凭证路径是开放集合.
# 表外默认: 它承担 workspace grant 的上界与 macOS 上的 deny-read 纵深.
# 漏一项的后果: 少一条纵深; 主要保护是"工作区外不授权"与围栏的读边界.
_CREDENTIAL_SUBPATHS = (
    # SSH, GPG 与通用凭证文件
    ".ssh",
    ".gnupg",
    ".netrc",
    ".password-store",
    ".local/share/keyrings",
    ".vault-token",
    # 云厂商
    ".aws",
    ".azure",
    ".config/gcloud",
    ".oci",
    ".config/doctl",
    ".config/rclone",
    # 容器与编排
    ".docker",
    ".kube",
    ".config/containers/auth.json",
    # 代码托管与包仓库 (这些文件里直接躺着 token)
    ".config/gh",
    ".config/glab-cli",
    ".git-credentials",
    ".config/git/credentials",
    ".npmrc",
    ".pypirc",
    ".gem/credentials",
    ".composer/auth.json",
    ".cargo/credentials.toml",
    ".m2/settings.xml",
    ".gradle/gradle.properties",
    ".subversion/auth",
    # 基础设施与数据库
    ".terraform.d",
    ".databrickscfg",
    ".pgpass",
    ".my.cnf",
    # 密码管理器 CLI
    ".config/op",
    # 浏览器 profile: cookie 与保存的密码都在里面, Agent 没有任何理由读它
    ".mozilla",
    ".config/google-chrome",
    ".config/chromium",
    "Library/Application Support/Google/Chrome",
    "Library/Application Support/Firefox",
    "Library/Cookies",
    "AppData/Roaming/Mozilla/Firefox/Profiles",
    "AppData/Local/Google/Chrome/User Data",
    # 平台自带的凭证存储
    "Library/Keychains",
    "AppData/Roaming/Microsoft/Credentials",
    "AppData/Local/Microsoft/Credentials",
    "AppData/Roaming/Microsoft/Crypto",
    "AppData/Roaming/gcloud",
)

# 会被**下一条命令自动读取**的配置. 收录判据就是这一条, 不是"能持久化".
#
# 为什么这条判据比"持久化"更要紧: 它们让目标集合分析失真. `curl https://x` 看起来只是
# 一次读取, 但 ~/.curlrc 里写一行 `output = /etc/cron.d/x`, 同一条命令就变成写系统
# 目录 —— 命令字符串没变, 审批界面上看到的东西也没变.
#
# 收进来的代价要先说清楚: 受保护路径的写入是 **Hard Deny** (workspace_analyzer 走
# denied 而不是 asked), 既不能批准也不能用 /add-dir 豁免. 所以判据符合还不够, 还得
# "人也确实没有理由在这里写".
#
# 按这条排除掉的:
#
# - 编辑器配置 (.vimrc / .config/nvim / .emacs.d): 只在人自己启动编辑器时执行, 而且
#   "帮我配一下 neovim"是合理请求.
# - ~/bin, ~/.local/bin: `pip install --user` 一类用户级安装的落点. 挡掉它等于逼用户
#   去用 sudo —— 那正好是我们最想避免的方向.
# - .gitconfig / .config/git/config: `git config --global user.email` 与
#   `gh auth setup-git` 都是正常操作. 它确实能经 core.hooksPath / alias 拿到执行,
#   但代价这边更重.
# - .cargo/config.toml: 配 crates.io 镜像是常规操作 (真正的凭证在
#   .cargo/credentials.toml, 那个仍然 deny_read).
# - .config/pip/pip.conf: 同理, 多数情况下只是一个镜像地址; 挡掉读取会让 Agent 没法
#   诊断安装问题.
# ADR-0040 B 类表: 写进去就等于下次开 shell 时执行任意代码的那些文件.
# 表外默认: 工作区外不可写.
# 漏一项的后果: 在隔离不足的平台上少一条纵深.
_STARTUP_SUBPATHS = (
    # Shell 启动文件. .zshenv 排第一位: 它连非交互 shell 都会读, 比 .zshrc 更危险,
    # 而 Agent 跑的每一条命令都是非交互 shell.
    ".zshenv",
    ".zshrc",
    ".zprofile",
    ".zlogin",
    ".config/zsh",
    ".bashrc",
    ".bash_profile",
    ".bash_login",
    ".profile",
    ".kshrc",
    ".cshrc",
    ".config/fish",
    # Agent 会真的去跑这些程序, 它们启动时读自己的配置
    ".curlrc",
    ".wgetrc",
    ".terraformrc",
    ".gdbinit",
    ".lldbinit",
    # 开机 / 登录自启
    "Library/LaunchAgents",
    ".config/systemd",
    ".config/autostart",
    ".config/environment.d",
    "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup",
    "Documents/WindowsPowerShell",
    "Documents/PowerShell",
)


def build_protected_path_policy(
    *, workspace_roots: tuple[str, ...] = ()
) -> ProtectedPathPolicy:
    """生成本机的受保护路径策略.

    workspace_roots 里的目录进豁免名单: 用户显式打开的目录按普通工作区处理, 但它**不能**
    豁免 Forge 自己的配置, 状态和凭证目录 —— 那些即使被 /add-dir 加进来也保持保护.
    """
    roots: list[ProtectedRoot] = []
    forge_home = config_dir()
    roots.extend(
        (
            ProtectedRoot(str(forge_home), ProtectedCategory.FORGE_CONFIG),
            ProtectedRoot(
                str(state_dir()), ProtectedCategory.FORGE_STATE, deny_read=True
            ),
            ProtectedRoot(
                str(forge_home / "credentials"),
                ProtectedCategory.FORGE_CREDENTIALS,
                deny_read=True,
            ),
            ProtectedRoot(str(forge_home / "runtime"), ProtectedCategory.FORGE_RUNTIME),
        )
    )
    roots.append(
        ProtectedRoot(_installed_package_root(), ProtectedCategory.FORGE_RUNTIME)
    )
    roots.extend(_platform_roots())
    homes = _home_roots()
    roots.extend(_user_roots(homes))
    roots.extend(_other_user_roots(homes))

    # 豁免只对"用户显式打开的工作区"生效, 且不能豁免 Forge 自身的数据目录.
    exemptions = tuple(
        root for root in workspace_roots if not _within_forge_home(root, forge_home)
    )
    return ProtectedPathPolicy(roots=_merged(roots), workspace_exemptions=exemptions)


def _merged(roots: list[ProtectedRoot]) -> tuple[ProtectedRoot, ...]:
    """同一路径被声明多次时合并, 且**只能收紧**.

    原来是 ``{root.path: root for ...}``, 后写覆盖先写 —— 一条笼统的规则可以把先声明的
    精确规则整个顶掉, 连 deny_read 都可能被顶成 False. 安全策略里去重的默认方向必须是
    更严, 而不是"看谁最后写".

    保留先声明的**分类** (先声明 = 更明确的意图), deny_read 取或.
    """
    merged: dict[str, ProtectedRoot] = {}
    for root in roots:
        if not root.path:
            continue
        existing = merged.get(root.path)
        if existing is None:
            merged[root.path] = root
        elif root.deny_read and not existing.deny_read:
            merged[root.path] = ProtectedRoot(
                existing.path, existing.category, deny_read=True
            )
    return tuple(merged.values())


def _installed_package_root() -> str:
    """已安装的 forgecli 包目录. 从模块自身位置解析, 不猜安装方式."""
    import forgecli

    package = Path(forgecli.__file__).resolve().parent
    return str(package)


def _platform_roots() -> list[ProtectedRoot]:
    if os.name == "nt":
        roots = [
            ProtectedRoot(path, ProtectedCategory.PLATFORM_SYSTEM)
            for path in windows_protected_directories()
        ]
        # 环境变量只能扩大保护集合，不能替代上面的 Windows API 结果。
        for variable in _WINDOWS_SYSTEM_VARS:
            value = os.environ.get(variable)
            if value:
                roots.append(ProtectedRoot(value, ProtectedCategory.PLATFORM_SYSTEM))
        return roots
    return [
        *(
            ProtectedRoot(path, ProtectedCategory.PLATFORM_SYSTEM)
            for path in _POSIX_SYSTEM
            if Path(path).exists()
        ),
        *(
            ProtectedRoot(path, ProtectedCategory.PLATFORM_DEVICE, deny_read=True)
            for path in _POSIX_DEVICES
            if Path(path).exists()
        ),
        *(
            ProtectedRoot(path, ProtectedCategory.OTHER_USER, deny_read=True)
            for path in _ROOT_HOMES
            if Path(path).exists()
        ),
    ]


def _home_roots() -> tuple[Path, ...]:
    """需要按"自己的家目录"来保护的所有目录.

    **不能用 ``Path.home()``.** 它等价于 ``expanduser("~")``, 在 POSIX 上先读 HOME 环境
    变量. HOME 会被改写, 最常见的就是 ``sudo``: 它把 HOME 重置成 root 的家目录, 于是
    ``~/.ssh`` 这批 deny_read 条目全部指向 ``/root/.ssh``, 而真正要保护的那个用户的密钥
    **整体掉出保护清单**. 那恰好是最不能出错的时候 —— 进程同时还拿着 root.

    所以这里返回一个**集合**而不是单个目录, 三个来源取并集:

    1. 口令数据库里真实 uid 对应的家目录 (``getuid`` 而非 ``geteuid``: sudo 下前者才是
       调用者). 这一项不受环境变量影响.
    2. ``SUDO_USER`` 指向的用户. 它是可以伪造的, 但伪造只会让保护范围**变大**.
    3. 环境变量说的那个家目录. 即使它被改写了, 改写后的位置也值得一并保护.

    取并集而不是"选一个正确的": 判断哪个才是真的会引入新的出错方式, 而多保护一个目录
    的代价只是多问一次.
    """
    candidates: list[Path] = []
    for source in (_passwd_home_of_real_uid(), _sudo_user_home(), _environment_home()):
        if source is not None:
            candidates.append(source)
    return _unique_dirs(candidates)


def _passwd_home_of_real_uid() -> Path | None:
    try:
        import pwd  # POSIX only

        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError, OSError):
        return None


def _sudo_user_home() -> Path | None:
    name = os.environ.get("SUDO_USER", "").strip()
    if not name:
        return None
    try:
        import pwd

        return Path(pwd.getpwnam(name).pw_dir)
    except (ImportError, KeyError, OSError):
        return None


def _environment_home() -> Path | None:
    raw = os.environ.get("HOME") or os.environ.get("USERPROFILE") or ""
    return Path(raw) if raw.strip() else None


def _unique_dirs(paths: list[Path]) -> tuple[Path, ...]:
    """去重并丢掉危险的退化值.

    过滤掉根目录与只有一段的路径: 一个取值为 ``/`` 的家目录会把整台机器标成受保护,
    Forge 会变成对每一次读取都拒绝 —— 那不是更安全, 那是不可用.
    """
    seen: dict[str, Path] = {}
    for path in paths:
        resolved = _safe_resolve(path)
        if resolved is None or len(resolved.parts) < 3:
            continue
        seen.setdefault(str(resolved), resolved)
    return tuple(seen.values())


def _safe_resolve(path: Path) -> Path | None:
    try:
        return path.expanduser().resolve()
    except (OSError, RuntimeError):
        return None


def _user_roots(homes: tuple[Path, ...]) -> list[ProtectedRoot]:
    roots: list[ProtectedRoot] = []
    for home in homes:
        roots.extend(
            ProtectedRoot(
                str(home / sub), ProtectedCategory.CREDENTIAL_STORE, deny_read=True
            )
            for sub in _CREDENTIAL_SUBPATHS
        )
        roots.extend(
            ProtectedRoot(str(home / sub), ProtectedCategory.STARTUP_CONFIG)
            for sub in _STARTUP_SUBPATHS
        )
    return roots


def _other_user_roots(homes: tuple[Path, ...]) -> list[ProtectedRoot]:
    """其他用户的家目录.

    原来的做法是扫**当前 home 的父目录**, 那在 home 落在标准位置时是对的, 一旦不是就会
    失控: root 的 home 是 ``/root``, 父目录就是 ``/``, 于是 ``/usr`` ``/etc`` ``/bin``
    统统被标成"其他用户的家目录"并附带 deny_read —— 连读一个系统库都要拦, 而且因为同名
    条目后写覆盖先写, 它们原本 "可读不可写" 的分类被直接顶掉了.

    改成扫固定的家目录容器. 容器是已知的几个位置, 与当前进程是谁无关.
    """
    protected = {str(home) for home in homes}
    roots: list[ProtectedRoot] = []
    for container in _HOME_CONTAINERS:
        for entry in _safe_iterdir(Path(container)):
            resolved = _safe_resolve(entry)
            if resolved is None or str(resolved) in protected:
                continue
            if not entry.is_dir():
                continue
            roots.append(
                ProtectedRoot(
                    str(resolved), ProtectedCategory.OTHER_USER, deny_read=True
                )
            )
    return roots


def _safe_iterdir(path: Path) -> tuple[Path, ...]:
    try:
        return tuple(path.iterdir())
    except OSError:
        return ()


def _within_forge_home(candidate: str, forge_home: Path) -> bool:
    try:
        Path(candidate).resolve().relative_to(forge_home.resolve())
    except (ValueError, OSError):
        return False
    return True
