"""人类审批的请求, 展示与绑定事实 (ADR-0013 §4.2 / §14).

三件事必须分清:

- ``ApprovalPresentation``: 用户**实际看到**的完整规范化视图. 用户批准的对象是它和它的
  哈希, 不是终端里那串被截断的命令.
- ``ApprovalBinding``: 批准当时绑定的事实快照. 任一项变化, 旧批准立即失效, 请求回到
  prepare -> 分析 -> 裁决, 必要时重新审批.
- ``ApprovalResponse``: 人类的决定. 它**不是执行授权** —— 批准之后还要重验, 建立恢复
  绑定, 才由 ToolAuthorizationService 签发一次性信封.

危险字段不做语义截断: 目标集合很大时可以分页或引用清单产物, 但必须给出完整条目数与
target_set_hash, 不能只显示前几项加一句"还有 N 个文件".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from forgecli.domain.intents import SessionMode
from forgecli.domain.security.script_facts import ScriptSnapshot
from forgecli.domain.security.vocabulary import ApprovalScope
from forgecli.domain.tool.catalog import ToolCatalog
from forgecli.domain.tool.hashing import digest
from forgecli.domain.tool.plan import ContentPreview, ToolPlan

__all__ = [
    "ApprovalBinding",
    "ApprovalOutcome",
    "ApprovalPresentation",
    "ApprovalRequest",
    "ApprovalResponse",
    "HitlApprovalView",
    "TargetGroup",
]


# 已封闭的 target_resolution 取值. 与 TargetResolution.closed 同一口径, 但这里存的是
# 字符串 —— 展示层不该为了判断封闭性去 import 工具层的枚举.
_CLOSED_RESOLUTIONS = frozenset({"static", "forge_expanded"})


class ApprovalOutcome(Enum):
    """审批结果. 超时和非交互环境保持 PENDING, 任何超时都不能转为批准."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


@dataclass(frozen=True)
class ApprovalBinding:
    """批准绑定的事实. 用 == 比较即可判断旧批准是否仍然成立."""

    tool_name: str
    spec_hash: str
    plan_hash: str
    target_set_hash: str
    capability_vocabulary_version: str
    catalog_snapshot_hash: str
    execution_profile_hash: str
    policy_version: str
    mode: SessionMode

    @classmethod
    def of(
        cls,
        plan: ToolPlan,
        *,
        catalog: ToolCatalog,
        execution_profile_hash: str,
        policy_version: str,
        mode: SessionMode,
    ) -> ApprovalBinding:
        return cls(
            tool_name=plan.tool_name,
            spec_hash=plan.spec_hash,
            plan_hash=plan.plan_hash,
            target_set_hash=plan.target_set_hash,
            capability_vocabulary_version=plan.capability_vocabulary_version,
            catalog_snapshot_hash=catalog.catalog_snapshot_hash,
            execution_profile_hash=execution_profile_hash,
            policy_version=policy_version,
            mode=mode,
        )

    @property
    def binding_hash(self) -> str:
        return digest(self)

    def differences(self, other: ApprovalBinding) -> tuple[str, ...]:
        """列出变化的绑定项, 用于告诉用户"为什么要重新批准"."""
        return tuple(
            name
            for name in (
                "tool_name",
                "spec_hash",
                "plan_hash",
                "target_set_hash",
                "capability_vocabulary_version",
                "catalog_snapshot_hash",
                "execution_profile_hash",
                "policy_version",
                "mode",
            )
            if getattr(self, name) != getattr(other, name)
        )


@dataclass(frozen=True)
class ApprovalPresentation:
    """审批界面必须展示的完整视图 (ADR-0013 §14).

    字段大多可选, 因为不同能力触发的审批内容不同; 但**一旦某项事实存在就必须展示**,
    不能因为界面窄而丢掉. 凭证值脱敏, 但凭证类型, 身份范围和目标主机不能隐藏.
    """

    action_summary: str
    user_intent_summary: str = ""
    workspace_roots: tuple[str, ...] = ()
    raw_command: str | None = None
    # 安全分析与授权实际绑定的那几份脚本正文. inline -c, heredoc, 临时脚本和脚本文件
    # 都先解析成它再展示 —— 只给路径等于让用户批准一个他没读过的文件.
    script_snapshots: tuple[ScriptSnapshot, ...] = ()
    # 写入目标**将会变成什么**. 只列路径是不够的: "写入 README.md"这句话里没有任何能
    # 让人做判断的信息.
    content_previews: tuple[ContentPreview, ...] = ()
    # 目标集合为什么没封闭. 空表示已封闭.
    unresolved_reason: str | None = None
    shell_kind: str | None = None
    executable_realpath: str | None = None
    executable_identity_hash: str | None = None
    interpreter_chain: tuple[str, ...] = ()
    cwd: str = ""
    mode: str = ""
    isolation_level: str = ""
    read_paths: tuple[str, ...] = ()
    write_paths: tuple[str, ...] = ()
    delete_paths: tuple[str, ...] = ()
    move_pairs: tuple[tuple[str, str], ...] = ()
    target_set_hash: str = ""
    target_resolution: str = ""
    network_targets: tuple[str, ...] = ()
    credential_scopes: tuple[str, ...] = ()
    external_effects: tuple[str, ...] = ()
    resolved_remote_targets: tuple[str, ...] = ()
    risk_facts: tuple[str, ...] = ()
    recovery_strategy: str = ""
    recovery_scope: str = ""
    checkpoint_id: str | None = None
    allowed_scopes: tuple[ApprovalScope, ...] = (ApprovalScope.ONCE,)
    invalidated_by: tuple[str, ...] = ()

    @property
    def presentation_hash(self) -> str:
        """用户批准的是这一份视图. 信封绑定它, 确认后不能静默扩大范围."""
        return digest(self)


@dataclass(frozen=True)
class ApprovalRequest:
    """一次待人类决定的请求. 创建它不等于允许执行, ShellTool 此时绝不能被调用."""

    approval_id: str
    binding: ApprovalBinding
    presentation: ApprovalPresentation
    mandatory: bool = False
    risk_facts: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        if not self.approval_id.strip():
            raise ValueError("ApprovalRequest.approval_id 不能为空")
        if self.mandatory and self.presentation.allowed_scopes != (ApprovalScope.ONCE,):
            # Mandatory Ask 只能创建与本次请求严格绑定的一次性审批: 不支持 session,
            # workspace 或 always (ADR-0013 §4.1).
            raise ValueError("Mandatory Ask 只能使用 once 范围")


@dataclass(frozen=True)
class TargetGroup:
    """一类目标的完整清单. paths 不截断, count 是真实条目数."""

    label: str
    paths: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.paths)


@dataclass(frozen=True)
class HitlApprovalView:
    """人类确认界面的最小 DTO (ADR-0016 §8.4).

    与 ApprovalPresentation 分开的理由: 后者是**授权重验**需要的完整事实, 里面的
    plan_hash, target_set_hash, 执行画像和失效字段对人做决定毫无帮助, 却会把真正要读的
    三样东西 (命令, 脚本, 目标) 淹掉. 所以这里做减法 —— 但减的是 hash, 不是效果.

    目标清单一旦要展示就必须完整: 用户批准的是这份效果清单, 不是命令字符串. `rm *.log`
    展开成 3 个还是 300 个文件, 是决定按不按同意的关键信息 (ADR-0013 §14 的这一条继续
    适用). 什么时候展示见 `consequential`.

    deny 不是 ApprovalScope 的取值 —— 它是"不授权", 没有范围可言, 因此不进
    allowed_scopes, 由界面固定提供.
    """

    mode: str
    workspace_roots: tuple[str, ...]
    raw_command: str
    target_groups: tuple[TargetGroup, ...] = ()
    target_resolution: str = ""
    script_snapshots: tuple[ScriptSnapshot, ...] = ()
    content_previews: tuple[ContentPreview, ...] = ()
    unresolved_reason: str | None = None
    recovery_strategy: str | None = None
    allowed_scopes: tuple[ApprovalScope, ...] = (ApprovalScope.ONCE,)

    @property
    def closed(self) -> bool:
        """目标集合是否封闭.

        判据是 `target_resolution` 本身, **不是** unresolved_reason 是否为空.
        后者曾经是判据, 而它是一个只在 `of()` 的关键字参数里出现、没有任何调用方传值的
        字段 —— 于是 closed 恒为真, 一个 DYNAMIC 的目标集合在界面上显示成已封闭, 未封闭
        原因与 checkpoint 提示一行都不会出现. 判据要用界面**已经拿到**的事实.
        """
        return self.target_resolution in _CLOSED_RESOLUTIONS

    @property
    def counts(self) -> tuple[tuple[str, int], ...]:
        """各类别条目数, 含为零的类别 —— "网络 0" 与"没提网络"对读者不是一回事."""
        return tuple((group.label, group.count) for group in self.target_groups)

    @property
    def consequential(self) -> bool:
        """这次动作除了读之外还会造成什么后果.

        纯读取时不展示目标清单: 用户已经逐字看到了命令与脚本, 再列一遍它会读哪些文件
        只是噪音. 但只要沾上写, 删, 移动, 网络, 外部副作用, 或者目标集合根本没封闭,
        清单就必须出现 —— 那些正是命令字符串看不出来的后果.
        """
        if not self.closed:
            return True
        return any(group.paths for group in self.target_groups if group.label != "读取")

    @classmethod
    def of(
        cls,
        presentation: ApprovalPresentation,
        *,
        allowed_scopes: tuple[ApprovalScope, ...] = (ApprovalScope.ONCE,),
        unresolved_reason: str | None = None,
    ) -> HitlApprovalView:
        groups = (
            TargetGroup("读取", presentation.read_paths),
            TargetGroup("写入", presentation.write_paths),
            TargetGroup("删除", presentation.delete_paths),
            TargetGroup(
                "移动",
                tuple(
                    f"{source} -> {target}"
                    for source, target in presentation.move_pairs
                ),
            ),
            TargetGroup("网络", presentation.network_targets),
            TargetGroup("外部副作用", presentation.external_effects),
        )
        return cls(
            mode=presentation.mode,
            workspace_roots=presentation.workspace_roots,
            # 没有原始命令的工具 (fs.write_patch 之类) 用动作摘要顶上, 但绝不留空:
            # 第二行是用户唯一能看懂"要发生什么"的地方.
            raw_command=presentation.raw_command or presentation.action_summary,
            target_groups=groups,
            target_resolution=presentation.target_resolution,
            script_snapshots=presentation.script_snapshots,
            content_previews=presentation.content_previews,
            unresolved_reason=unresolved_reason or presentation.unresolved_reason,
            recovery_strategy=presentation.recovery_strategy or None,
            allowed_scopes=allowed_scopes,
        )


@dataclass(frozen=True)
class ApprovalResponse:
    outcome: ApprovalOutcome
    approval_id: str
    scope: ApprovalScope = ApprovalScope.ONCE
    note: str = ""

    def __post_init__(self) -> None:
        if self.outcome is not ApprovalOutcome.APPROVED and self.scope.learned:
            raise ValueError("只有批准才能带学习式授权范围")

    @property
    def approved(self) -> bool:
        return self.outcome is ApprovalOutcome.APPROVED
