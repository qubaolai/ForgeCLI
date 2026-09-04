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
]

# 词汇表版本. 改动词汇一律要走 ADR, 但**要不要升版本取决于改的是哪一种** (ADR-0033
# 决策 9):
#
# - **增量扩词汇** (加一个新值, 不动任何已有值的含义): 不升, 也不失效任何东西.
#   本常量全库只有一个消费方 —— ToolPlan 的同名字段默认值, 它进 _hash_source 因而进
#   plan_hash. 加一个新枚举值不改变任何**已有** plan 的哈希: 老的 always 规则是给不
#   声明这个新能力的工具记的, 照常匹配; 声明它的是新工具, 没有旧规则指着它. 升版本
#   反而会把每一个 plan_hash 打乱, 让用户为一件与他无关的事重新批准一遍.
# - **语义改动** (改写或复用一个已有值): 必须升版本, 并使下游缓存与学习规则失效.
#   那时一条老规则可能匹配上一件它当初没批准过的事, 而这是静默的.
#
# 判据是"老规则会不会因此匹配到新东西", 不是"枚举变没变".
CAPABILITY_VOCABULARY_VERSION = "1"


class Capability(Enum):
    """本次调用请求的能力. 取值会进哈希, 不可改."""

    PLAN_ONLY = "plan_only"
    # 读回自己落盘的工具输出 (ADR-0032 决策 6.2). 与 PLAN_ONLY 同侧: 读的是 Forge
    # 自己的状态目录, 碰不到用户文件, 目标也不由模型指定 —— 入参是一个内容哈希,
    # 不是路径.
    ARTIFACT_READ = "artifact_read"
    # 写跨会话记忆 (ADR-0033 决策 9). 同样写 ~/.forge/ 下的文件, 同样不算 mutating.
    #
    # 不复用 PLAN_ONLY: 复用会让安全矩阵里"计划"和"记忆"变成同一件事, 而将来任何一条
    # 只想管其中一个的规则都写不出来.
    MEMORY_WRITE = "memory_write"
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
# SPAWN_PROCESS 不在其中: 一个上界窄且只读的工具可以靠子进程取数据 (曾经的 git_read
# 就是这么做的), 挡掉它会让这类工具进不了 plan 档, 而 ADR-0013 §1 要的正是它们在那一档
# 可用. 真正危险的子进程一定同时声明 EXECUTE_SHELL / EXECUTE_SCRIPT, 那两项在表里.
#
# 目前没有工具落在这个位置 —— 只读子进程那一类已经交给 shell_run. 这一条因此暂时没有
# 消费方, 但它是**表的口径**而不是某个工具的例外: 判据是"子进程本身不等于会改状态".
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
