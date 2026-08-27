"""受保护路径策略 (ADR-0014 §5.1).

沙箱层本次不实现, 但这条规则与沙箱无关: 它是 Hard Deny 的判据, 在无隔离环境下反而是
唯一的路径边界. `/add-dir`, 学习式 Allow, `full_access` 和风险分类器**都不能**降低它的
优先级.

判定只看 **realpath**. 字符串前缀匹配挡不住三类常见绕过:

    /work/proj/link -> /etc         符号链接
    /tmp -> /private/tmp            macOS 别名
    C:\\Users\\x  vs  c:\\users\\X    大小写与分隔符差异

区分两件容易混的事: 不可变的 Forge 运行时目录 (可执行文件, 已安装包, 配置, 凭证, 状态)
始终受保护; 而**用户显式打开的 Forge 源码仓库**按普通工作区处理 —— 否则用 Forge 开发
Forge 自己就成了违规操作.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import PurePath

from forgecli.domain.tool.hashing import digest
from forgecli.domain.workspace.boundary import is_within

__all__ = [
    "HARMLESS_DEVICES",
    "ProtectedCategory",
    "ProtectedPathPolicy",
    "ProtectedRoot",
    "PathVerdict",
    "is_harmless_device",
]

# 进程自己的流与丢弃口. 它们落在 /dev 下, 但读写它们既碰不到块设备, 也拿不到任何别人
# 的数据.
#
# 不放行的话 `2>/dev/null` 就是一次"写入受保护路径", 而那是 Hard Deny —— 一批纯只读
# 命令因此连人点头都放不行. 真实日志里模型想查一下 maven 装在哪, 两次都被这条拦下, 于是
# 它再也没能验证自己写的代码能不能构建.
HARMLESS_DEVICES = frozenset(
    {"/dev/null", "/dev/stdin", "/dev/stdout", "/dev/stderr", "/dev/tty"}
)
_HARMLESS_DEVICE_PREFIX = "/dev/fd/"


def is_harmless_device(path: str) -> bool:
    """写它不改变任何持久状态, 读它也读不到别人的东西."""
    return path in HARMLESS_DEVICES or path.startswith(_HARMLESS_DEVICE_PREFIX)


class ProtectedCategory(Enum):
    """受保护类别"""

    FORGE_RUNTIME = "forge_runtime"
    FORGE_CONFIG = "forge_config"
    FORGE_STATE = "forge_state"
    FORGE_CREDENTIALS = "forge_credentials"
    CREDENTIAL_STORE = "credential_store"
    PLATFORM_SYSTEM = "platform_system"
    PLATFORM_DEVICE = "platform_device"
    STARTUP_CONFIG = "startup_config"
    OTHER_USER = "other_user"


# 可以被"用户显式打开的工作区"豁免的类别. 只有一个: editable 安装把 Forge 的包目录
# 指到了源码仓库, 而用 Forge 开发 Forge 是正当场景. 凭证, 系统, 设备, 启动配置和
# 其他用户的目录**永远不可豁免** —— 把工作区设成 `~` 或 `/` 不应该解除任何保护.
_EXEMPTIBLE_CATEGORIES: frozenset[ProtectedCategory] = frozenset(
    {ProtectedCategory.FORGE_RUNTIME}
)


class PathVerdict(Enum):
    """对某个路径的访问结论. 只有 ALLOWED 表示"这条规则不拦它"."""

    ALLOWED = "allowed"
    DENY_WRITE = "deny_write"
    DENY_READ = "deny_read"


@dataclass(frozen=True)
class ProtectedRoot:
    """一段受保护路径.

    deny_read 单独标: 系统目录通常允许只读 (按模式进 ASK 或受限 Allow), 但凭证, 密钥,
    设备和 Forge 内部数据的**读取**本身就是外泄, 必须直接拒绝.
    """

    path: str
    category: ProtectedCategory
    deny_read: bool = False


@dataclass(frozen=True)
class ProtectedPathPolicy:
    """启动时生成, 运行期只读. Agent 与 /add-dir 都无法修改它."""

    roots: tuple[ProtectedRoot, ...]
    # 用户显式打开的工作区: 即使它落在某个受保护根下 (例如把 Forge 源码仓库
    # clone 到了 ~/.forge 旁边), 也按普通工作区处理.
    workspace_exemptions: tuple[str, ...] = ()

    @property
    def protected_roots_hash(self) -> str:
        """进执行画像. 保护集合变了, 旧授权与旧审批全部失效."""
        return digest(
            {
                "roots": [
                    (root.path, root.category.value, root.deny_read)
                    for root in sorted(self.roots, key=lambda item: item.path)
                ],
                "exemptions": tuple(sorted(self.workspace_exemptions)),
            }
        )

    def classify(self, realpath: str) -> ProtectedRoot | None:
        """路径落在哪个受保护根下. 命中多个时取最深的那个 (最具体的规则优先).

        豁免**只对 _EXEMPTIBLE_CATEGORIES 生效**, 且在取最深匹配之前逐条筛掉, 而不是
        一命中豁免就整体返回 None. 早先是后者, 后果是把主目录当工作区打开时,
        `~/.ssh`, `~/.aws`, `~/.zshrc` 全部变成未受保护 —— 一条为"用 Forge 开发 Forge"
        准备的豁免, 顺手关掉了凭证与启动配置的保护.
        """
        if is_harmless_device(realpath):
            return None
        exempt = self._exempt(realpath)
        matches = [
            root
            for root in self.roots
            if is_within(realpath, root.path)
            and not (exempt and root.category in _EXEMPTIBLE_CATEGORIES)
        ]
        if not matches:
            return None
        return max(matches, key=lambda root: len(PurePath(root.path).parts))

    def verdict_for_read(self, realpath: str) -> PathVerdict:
        root = self.classify(realpath)
        if root is None:
            return PathVerdict.ALLOWED
        return PathVerdict.DENY_READ if root.deny_read else PathVerdict.ALLOWED

    def verdict_for_write(self, realpath: str) -> PathVerdict:
        """受保护路径的写入一律拒绝, 不分类别, 不看模式."""
        return (
            PathVerdict.ALLOWED
            if self.classify(realpath) is None
            else PathVerdict.DENY_WRITE
        )

    def _exempt(self, realpath: str) -> bool:
        return any(
            is_within(realpath, exemption) for exemption in self.workspace_exemptions
        )
