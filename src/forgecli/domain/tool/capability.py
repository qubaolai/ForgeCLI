"""能力闭集词汇 (ADR-0004 §5).

这是工具系统与安全模块解耦的核心: 安全模块**不认识任何具体工具类型**, 只认识能力,
按能力分派分析器. 因此新增工具不必改安全模块, 新增规则也不必改工具实现.

词汇是闭集并带版本. 工具声明了当前版本无法识别的能力时归一为 UNKNOWN 走未知路径,
而不是因为"不认识"就跳过 —— 漏判的默认后果必须是更保守, 不是放行.
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "CAPABILITY_VOCABULARY_VERSION",
    "MUTATING_CAPABILITIES",
    "Capability",
    "normalize_capability",
]

# 词汇表版本. 新增或改写词汇需要 ADR 或显式版本升级, 并使下游缓存与学习规则失效.
CAPABILITY_VOCABULARY_VERSION = "1"


class Capability(Enum):
    """本次调用请求的能力. 取值会进哈希与审计, 不可改."""

    PLAN_ONLY = "plan_only"
    WORKSPACE_READ = "workspace_read"
    WORKSPACE_WRITE = "workspace_write"
    WORKSPACE_DELETE = "workspace_delete"
    PATH_MOVE = "path_move"
    EXTERNAL_READ = "external_read"
    EXTERNAL_WRITE = "external_write"
    CREDENTIAL_ACCESS = "credential_access"
    EXECUTE_SHELL = "execute_shell"
    EXECUTE_SCRIPT = "execute_script"
    SPAWN_PROCESS = "spawn_process"
    NETWORK_ACCESS = "network_access"
    EXTERNAL_IRREVERSIBLE_EFFECT = "external_irreversible_effect"
    MODEL_CALL = "model_call"
    UNKNOWN = "unknown"


# 会改变状态或触达工具自身边界之外的能力. 能力门 (application 侧的目录谓词) 用它把
# 写, 删, 执行, 网络和未知挡在 plan 档之外.
#
# SPAWN_PROCESS 不在其中: git.read 这类工具靠子进程读数据, 但上界窄且
# 只读, 挡掉它会让 ADR-0013 §1 明确要求出现在 plan 档的 GitReadTool
# 反而进不去. 真正危险的子进程一定同时声明 EXECUTE_SHELL / EXECUTE_SCRIPT.
MUTATING_CAPABILITIES = frozenset(
    {
        Capability.WORKSPACE_WRITE,
        Capability.WORKSPACE_DELETE,
        Capability.PATH_MOVE,
        Capability.EXTERNAL_WRITE,
        Capability.CREDENTIAL_ACCESS,
        Capability.EXECUTE_SHELL,
        Capability.EXECUTE_SCRIPT,
        Capability.NETWORK_ACCESS,
        Capability.EXTERNAL_IRREVERSIBLE_EFFECT,
        Capability.UNKNOWN,
    }
)


def normalize_capability(raw: str) -> Capability:
    """把外部 (MCP / skill / 配置) 声明的能力名归一为闭集成员.

    不认识的一律归 UNKNOWN, 由 ADR-0013 的未知路径处理 —— 不抛错, 因为抛错会让一个
    拼错能力名的 MCP server 把整条链路打断, 而静默丢弃又等于放行.
    """
    try:
        return Capability(raw.strip().lower())
    except ValueError:
        return Capability.UNKNOWN
