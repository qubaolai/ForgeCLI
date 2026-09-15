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

from forgecli.common.errors import ForgeError

__all__ = [
    "APPROVAL_OPTIONS",
    "MODE_PRESETS",
    "PRESET_NAMES",
    "SANDBOX_OPTIONS",
    "ApprovalPolicy",
    "InputOrigin",
    "ModeUnavailableError",
    "SandboxLevel",
    "SessionMode",
    "StanceOption",
    "available_presets",
    "stance_label",
]


class InputOrigin(Enum):
    """这一行输入是谁给的。

    人类说的话与程序合成的话必须分得开: 计划评审的"同意并执行"会以本轮的名义再入一次
    turn, 那句话是**程序**给的 (plan_review), 不能算用户又说了一遍。

    默认值是 PROGRAM: 新增一个调用方时, 忘记传参的后果是少算一次人类输入, 不是多算。

    早先还有一档 ``TTY_USER``, 承载人工 Shell 的特权 (ADR-0017 §2)。人工 Shell 随终端
    入口一起删除之后, 没有任何代码路径能再产出它, 所以它也不该继续留在这张表里 —— 一个
    永远取不到的权限档只会让读的人以为它还在用。老 events.jsonl 里可能留着
    ``"tty_user"`` 这个字符串, 但没有任何地方把它解析回枚举。ADR-0045 把终端入口加了
    回来, 但它取的是 ``CLI_USER``: 那一档只标"这句话是谁说的", 不带任何权限。
    """

    # 经本地 Web 控制面的认证用户。
    WEB_USER = "web_user"
    # 经终端交互式会话的用户 (ADR-0045)。与 WEB_USER 分开记, 是因为它进 events.jsonl:
    # 事后回看一条会话时, "这句话是在浏览器里说的还是在终端里说的"是查不回来的事实,
    # 合成一档就等于把它丢了。两者在裁决与提示词上没有任何差别 —— 早先 TTY_USER 承载的
    # 人工 Shell 特权不随本档回来。
    CLI_USER = "cli_user"
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

    四档从紧到松: MANUAL, ALWAYS, AUTO, NEVER. 次序写在这里而不是靠声明顺序推断,
    `domain/execution/fence` 按名字逐档编译, 不做大小比较.
    """

    #: 每一次读写都要点头: fs_read / search_text 的读, fs_apply_patch 的写, 以及每一条
    #: Shell 命令 —— 不管 Forge 看不看得清它. 只有不碰工作区文件的那几类调用 (写计划,
    #: 向人提问, 记忆, 读回自己落盘的输出) 仍然自动. 对应 Claude Code 的 Manual 档,
    #: 但比它更严: 那边读文件不问, 这边问.
    MANUAL = "manual"
    #: 只放行 Forge 看得清的那一类: 命令解析完整, 目标集合封闭, 且触达的东西都在围栏内
    #: (ADR-0046). 看不透要跑什么的 —— 脚本正文, `npm test`, `find -exec`, 联网 ——
    #: 仍要点头. 判据是**这条命令碰了什么**, 不是"它是不是一次 shell 调用".
    ALWAYS = "always"
    #: 围栏内不必先看清楚: 看不透的执行也自动放行. 越界的仍然要问.
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

    MANUAL: ClassVar[SessionMode]
    PLAN: ClassVar[SessionMode]
    ACCEPT_EDITS: ClassVar[SessionMode]
    AUTO: ClassVar[SessionMode]
    FULL_ACCESS: ClassVar[SessionMode]

    @property
    def value(self) -> str:
        """两个轴拼成一个稳定字符串, 进日志, 事件载荷与规则持久化."""
        return f"{self.sandbox.value}/{self.approval.value}"

    @property
    def unrestricted(self) -> bool:
        """围栏就是全部边界, 裁决侧除 Hard Deny 外不再另设闸 (ADR-0030 决策 4 修订).

        这是唯一一个**启动时没声明就进不去**的姿态 (ADR-0052):
        `forge --allow-full-access` 才把它放进可选预设, 否则 Tab 循环, `/mode` 与
        `POST /api/v1/mode` 都拿不到它.
        判据是审批档而不是预设名: 一个"围栏内不问"的姿态无论叫什么名字都该过这道门.
        """
        return self.approval is ApprovalPolicy.NEVER

    @classmethod
    def from_value(cls, raw: str) -> SessionMode:
        """解析 `value`, 也认预设名.

        两种写法各有各的用途: 预设名是界面在用的 —— `/mode auto` 与 `POST /api/v1/mode`
        的 `{"mode": "auto"}` 都是一次点击换整档; `sandbox/approval` 整串只出现在落盘的
        学习规则里 (`json_learned_rules_store`), 界面不再按轴调 (ADR-0052).

        认不出就抛 ValueError. 已落盘的学习规则里可能存着更早的写法, 那时
        `json_learned_rules_store._rule_of` 会把那一条跳过 —— 后果是那条规则失效, 不是
        整个规则库加载失败, 而它们在执行画像变更之后本来也已经不再命中.
        """
        preset = PRESET_NAMES.get(raw)
        if preset is not None:
            return preset
        sandbox, _, approval = raw.partition("/")
        return cls(sandbox=SandboxLevel(sandbox), approval=ApprovalPolicy(approval))


SessionMode.MANUAL = SessionMode(SandboxLevel.WORKSPACE_WRITE, ApprovalPolicy.MANUAL)
SessionMode.PLAN = SessionMode(SandboxLevel.READ_ONLY, ApprovalPolicy.ALWAYS)
SessionMode.ACCEPT_EDITS = SessionMode(
    SandboxLevel.WORKSPACE_WRITE, ApprovalPolicy.ALWAYS
)
SessionMode.AUTO = SessionMode(SandboxLevel.WORKSPACE_WRITE, ApprovalPolicy.AUTO)
SessionMode.FULL_ACCESS = SessionMode(SandboxLevel.FULL_ACCESS, ApprovalPolicy.NEVER)

# 预设的名字, 按**自主性**从低到高: 每一步都让 Forge 少问一类事. 次序是显式写出的而
# 不是靠声明顺序: 安全相关的排序不该靠别处的巧合.
#
# manual 排在 plan 前面而不是后面, 虽然它的围栏 (工作区可写) 比 plan (只读) 宽: 这条
# 梯度量的是"有多少事不问人就做了", manual 一件都不做. 与 Claude Code 的 Shift+Tab
# 循环同一个次序 (Manual -> Accept edits -> Plan 的前两项), plan 放第二是因为它在
# ForgeCLI 里是"只出方案"的起点档, 计划评审从它升到 auto (ADR-0038).
#
# 模式定义是代码级常量, 配置只能收紧 (`security.disable_full_access`), 不能放宽.
PRESET_NAMES: dict[str, SessionMode] = {
    "manual": SessionMode.MANUAL,
    "plan": SessionMode.PLAN,
    "accept_edits": SessionMode.ACCEPT_EDITS,
    "auto": SessionMode.AUTO,
    "full_access": SessionMode.FULL_ACCESS,
}


class ModeUnavailableError(ForgeError):
    """要切的姿态没有在启动时放开 (ADR-0052). message 可直接展示."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class StanceOption:
    """一个档位在界面上的样子: 值, 名字, 一句说明.

    文案跟着定义走, 与 ConfigKey 的 label/help 同一个理由 (domain/config/config_keys):
    同一个档位在终端菜单和网页上叫不同的名字, 两边都不会报错, 只会让用户以为它们是两个
    开关. 界面照着这张表渲染, 不自己写一份.
    """

    value: str
    label: str
    hint: str


SANDBOX_OPTIONS: tuple[StanceOption, ...] = (
    StanceOption(SandboxLevel.READ_ONLY.value, "只读", "工作区不可写, 只能看"),
    StanceOption(
        SandboxLevel.WORKSPACE_WRITE.value, "工作区可写", "能改文件, 不能联网"
    ),
    StanceOption(SandboxLevel.FULL_ACCESS.value, "完全访问", "能改文件, 也能联网"),
)

APPROVAL_OPTIONS: tuple[StanceOption, ...] = (
    StanceOption(ApprovalPolicy.MANUAL.value, "逐次确认", "每一次读写都要你点头"),
    StanceOption(ApprovalPolicy.ALWAYS.value, "每次确认", "Shell 命令都要你点头"),
    StanceOption(ApprovalPolicy.AUTO.value, "围栏内自动", "越界的才问你"),
    StanceOption(ApprovalPolicy.NEVER.value, "不再确认", "围栏就是全部边界"),
)

# 预设在界面上的样子. 终端 `/mode`, Tab 循环与 Web 的模式菜单**只列这张表**, 两个轴
# 不再单独露出 (ADR-0052): 四档预设已经覆盖了有用例的组合, 而"强隔离 + 全自动"这类
# 自由组合在菜单里只是多一层要解释的东西. 次序与 PRESET_NAMES 一致.
MODE_PRESETS: tuple[StanceOption, ...] = (
    StanceOption("manual", "Manual", "每一次读写都先询问"),
    StanceOption("plan", "Plan", "只出方案, 先评审再动手"),
    StanceOption("accept_edits", "Accept Edits", "自动接受文件编辑"),
    StanceOption("auto", "Auto", "自动执行, 风险操作仍需审批"),
    StanceOption("full_access", "Full Access", "打断最少, 权限最大"),
)


def available_presets(*, full_access_allowed: bool) -> tuple[StanceOption, ...]:
    """本进程能进的预设, 按 MODE_PRESETS 的次序.

    `full_access_allowed` 是启动时定下的事实 (`forge --allow-full-access`, ADR-0052),
    整个进程生命周期不变. 没放开时 Full Access 不出现在任何菜单里, 而不是出现了再拒绝:
    一个点了会报错的选项比没有这个选项更让人困惑.
    """
    return tuple(
        preset
        for preset in MODE_PRESETS
        if full_access_allowed or not PRESET_NAMES[preset.value].unrestricted
    )


def stance_label(mode: SessionMode) -> str:
    """命中预设就用预设名, 否则把两个轴拼出来 —— 不编一个不存在的档位名."""
    for preset in MODE_PRESETS:
        if PRESET_NAMES[preset.value] == mode:
            return preset.label
    sandbox = next(
        (item.label for item in SANDBOX_OPTIONS if item.value == mode.sandbox.value),
        mode.sandbox.value,
    )
    approval = next(
        (item.label for item in APPROVAL_OPTIONS if item.value == mode.approval.value),
        mode.approval.value,
    )
    return f"{sandbox} · {approval}"
