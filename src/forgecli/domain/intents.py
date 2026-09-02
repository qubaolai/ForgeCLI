"""会话输入的两个领域枚举: 这一行是谁给的, 当前是哪一档模式。

原先这里还住着一组 ``UserIntent`` 值对象 (UserMessage / SlashCommand /
ManualShellIntent / UnknownCommand). 它们的唯一解析方 IntentRouter 与唯一执行方
斜杠命令注册表都在终端入口里, 随 ADR-0025 决策 1 的二次修订一起删除 —— Web 控制面
不解析文本意图: 消息就是消息, 配置项各有各的接口。

设计约束: 不可变, 值相等, 无副作用; 取值会落盘 (mode 进 state.json, origin 进
user_message 事件), 因此不可随意改写。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar

__all__ = [
    "PRESET_NAMES",
    "ApprovalPolicy",
    "InputOrigin",
    "SandboxLevel",
    "SessionMode",
]


class InputOrigin(Enum):
    """这一行输入是谁给的。

    人类说的话与程序合成的话必须分得开: 计划评审的"同意并执行"会以本轮的名义再入一次
    turn, 那句话是**程序**给的 (plan_review), 不能算用户又说了一遍。

    默认值是 PROGRAM: 新增一个调用方时, 忘记传参的后果是少算一次人类输入, 不是多算。

    早先还有一档 ``TTY_USER``, 承载人工 Shell 的特权 (ADR-0017 §2)。人工 Shell 随终端
    入口一起删除之后, 没有任何代码路径能再产出它, 所以它也不该继续留在这张表里 —— 一个
    永远取不到的权限档只会让读的人以为它还在用。老 events.jsonl 里可能留着
    ``"tty_user"`` 这个字符串, 但没有任何地方把它解析回枚举。
    """

    # 经本地 Web 控制面的认证用户。
    WEB_USER = "web_user"
    PROGRAM = "program"


class SandboxLevel(Enum):
    """围栏允许什么: 能写哪里, 能不能联网.

    与审批轴正交. "隔离多强"与"什么时候问人"是两个独立的问题, 早先它们被压在一个四档
    枚举里, 于是"强隔离 + 全自动"和"弱隔离 + 每步都问"这两种组合根本表达不出来.
    """

    #: 工作区不可写. 只能看, 不能改.
    READ_ONLY = "read_only"
    #: 工作区可写, 不能联网.
    WORKSPACE_WRITE = "workspace_write"
    #: 工作区可写, 可以联网.
    FULL_ACCESS = "full_access"


class ApprovalPolicy(Enum):
    """什么时候要人点头.

    它管的是**裁决层的自主性**, 不是操作系统围栏 —— 围栏该拦的照拦, 这一轴只决定
    Forge 自己要不要在拦之前先问一次.
    """

    #: 需要能力的调用都先问. 文件工具照常自动, Shell 必须点头.
    ALWAYS = "always"
    #: 围栏内可以自动启动 Shell. 越界的仍然要问.
    AUTO = "auto"
    #: 除 Hard Deny 外不再另设闸, 围栏就是全部边界.
    NEVER = "never"


@dataclass(frozen=True)
class SessionMode:
    """一次会话的姿态: 隔离档 x 审批档.

    两个轴可以任意组合, 四个预设只是常用组合的名字 (Tab 循环用它们). 早先这是一个四档
    枚举, 两个轴被绑死在一条线上.

    **不落盘进 state.json** (见 `domain/session/snapshot`: 模式是运行时状态而非会话
    史实). 但它进 learned rule 的匹配事实, 所以 `value` 必须是稳定可解析的.
    """

    sandbox: SandboxLevel
    approval: ApprovalPolicy

    PLAN: ClassVar[SessionMode]
    ACCEPT_EDITS: ClassVar[SessionMode]
    AUTO: ClassVar[SessionMode]
    FULL_ACCESS: ClassVar[SessionMode]

    @property
    def value(self) -> str:
        """两个轴拼成一个稳定字符串, 进日志, 事件载荷与规则持久化."""
        return f"{self.sandbox.value}/{self.approval.value}"

    @classmethod
    def from_value(cls, raw: str) -> SessionMode:
        """解析 `value`, 也认四个预设名.

        预设名是**在用的接口快捷方式**, 不是遗留兼容: `POST /api/v1/mode` 收
        `{"mode": "auto"}` 就是一次点击换整档, 而按轴调是 `{"sandbox": ...}`.

        认不出就抛 ValueError. 已落盘的学习规则里可能存着更早的写法, 那时
        `json_learned_rules_store._rule_of` 会把那一条跳过 —— 后果是那条规则失效, 不是
        整个规则库加载失败, 而它们在执行画像变更之后本来也已经不再命中.
        """
        preset = PRESET_NAMES.get(raw)
        if preset is not None:
            return preset
        sandbox, _, approval = raw.partition("/")
        return cls(sandbox=SandboxLevel(sandbox), approval=ApprovalPolicy(approval))


SessionMode.PLAN = SessionMode(SandboxLevel.READ_ONLY, ApprovalPolicy.ALWAYS)
SessionMode.ACCEPT_EDITS = SessionMode(
    SandboxLevel.WORKSPACE_WRITE, ApprovalPolicy.ALWAYS
)
SessionMode.AUTO = SessionMode(SandboxLevel.WORKSPACE_WRITE, ApprovalPolicy.AUTO)
SessionMode.FULL_ACCESS = SessionMode(SandboxLevel.FULL_ACCESS, ApprovalPolicy.NEVER)

# 预设的名字, 权限从紧到松. 次序是显式写出的而不是靠声明顺序: 安全相关的排序不该
# 靠别处的巧合. ADR-0009 决策 13: 模式定义是代码级常量, 配置只能收紧, 不能放宽.
PRESET_NAMES: dict[str, SessionMode] = {
    "plan": SessionMode.PLAN,
    "accept_edits": SessionMode.ACCEPT_EDITS,
    "auto": SessionMode.AUTO,
    "full_access": SessionMode.FULL_ACCESS,
}
