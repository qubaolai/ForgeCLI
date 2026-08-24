"""人类审批的视图、绑定与响应 (ADR-0013 §4.2 / §14, ADR-0028 规则 B / C).

三件事必须分清:

- ``ApprovalView``: 用户**实际看到**的完整规范化视图. 用户批准的对象是它和它的
  ``view_hash``, 不是终端里那串被截断的命令.
- ``ApprovalBinding``: 批准当时绑定的事实快照. 任一项变化, 旧批准立即失效, 请求回到
  prepare -> 分析 -> 裁决, 必要时重新审批.
- ``ApprovalResponse``: 人类的决定. 它**不是执行授权** —— 批准之后还要重验, 建立恢复
  绑定, 才由 ToolAuthorizationService.issue 签发一次性 ExecutionAuthorization.

危险字段不做语义截断: 目标集合很大时可以分页或引用清单产物, 但必须给出完整条目数与
target_set_hash, 不能只显示前几项加一句"还有 N 个文件".

**ADR-0028 之前这里有两个类**: ``ApprovalPresentation`` (30 个字段, 供授权重验) 与
``HitlApprovalView`` (供界面渲染), 后者是前者唯一的消费方. 拆成两个的理由是"重验要的
哈希会把人要读的东西淹掉" —— 但那是**渲染**该解决的问题, 不该由类型系统表达成两份
互相抄写的字段. 合并之后, 界面读什么由渲染函数决定, 而重验读的 ``view_hash`` 与人看到
的内容之间不再隔着一次字段搬运.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from forgecli.domain.intents import SessionMode
from forgecli.domain.security.scripts import ScriptSnapshot
from forgecli.domain.security.vocabulary import ApprovalScope
from forgecli.domain.tool.catalog import ToolCatalog
from forgecli.domain.tool.hashing import digest
from forgecli.domain.tool.plan import ContentPreview, ShellSubject, ToolPlan

__all__ = [
    "ApprovalBinding",
    "ApprovalOutcome",
    "ApprovalRequest",
    "ApprovalResponse",
    "ApprovalView",
    "TargetGroup",
]


class ApprovalOutcome(Enum):
    """审批结果. 超时和非交互环境保持 PENDING, 任何超时都不能转为批准."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


@dataclass(frozen=True)
class ApprovalBinding:
    """批准绑定的事实. 用 == 比较即可判断旧批准是否仍然成立.

    **只存 plan 之外的事实** (ADR-0028 规则 B). 早先这里还单独存了 tool_name,
    spec_hash, target_set_hash 与 capability_vocabulary_version —— 四项全部已经在
    ``ToolPlan._hash_source`` 里, plan_hash 变了它们必然被覆盖. 存第二份不构成第二道
    防线, 只是多了一份必须与 ``_hash_source`` 手工保持同步的字段清单, 而漏同步不会报错.
    """

    plan_hash: str
    catalog_snapshot_hash: str
    execution_profile_hash: str
    policy_version: str
    mode: SessionMode
    view_hash: str

    @classmethod
    def of(
        cls,
        plan: ToolPlan,
        *,
        catalog: ToolCatalog,
        execution_profile_hash: str,
        policy_version: str,
        mode: SessionMode,
        view_hash: str,
    ) -> ApprovalBinding:
        return cls(
            plan_hash=plan.plan_hash,
            catalog_snapshot_hash=catalog.catalog_snapshot_hash,
            execution_profile_hash=execution_profile_hash,
            policy_version=policy_version,
            mode=mode,
            view_hash=view_hash,
        )

    @property
    def binding_hash(self) -> str:
        return digest(self)

    def differences(self, other: ApprovalBinding) -> tuple[str, ...]:
        """列出变化的绑定项, 用于告诉用户"为什么要重新批准"."""
        return tuple(
            name
            for name in (
                "plan_hash",
                "catalog_snapshot_hash",
                "execution_profile_hash",
                "policy_version",
                "mode",
                "view_hash",
            )
            if getattr(self, name) != getattr(other, name)
        )


@dataclass(frozen=True)
class TargetGroup:
    """一类目标的完整清单. paths 不截断, count 是真实条目数."""

    label: str
    paths: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.paths)


@dataclass(frozen=True)
class ApprovalView:
    """人类确认界面看到的完整视图, 同时是授权重验绑定的对象 (ADR-0013 §14).

    路径, 目标封闭度, cwd 与写入内容全部**从 plan 读**, 不在这里另存一份 —— 它们已经
    由 ``plan_hash`` 绑定, 抄一遍只会制造"视图说写 3 个文件, plan 说写 5 个"这种非法
    状态. 这里只放 plan 里没有的东西: 人类语境 (用户这句话想干什么, 工作区在哪), 分析
    结论 (脚本正文, 风险事实, 未封闭原因) 和界面提供的选项.
    """

    plan: ToolPlan
    action_summary: str
    # 展示用. 不进 view_hash: mode 的权威事实在 ApprovalBinding.mode, 那里已经会因为
    # 模式切换而使旧批准失效. 两处都算等于同一个事实存两遍.
    mode: str = ""
    user_intent_summary: str = ""
    workspace_roots: tuple[str, ...] = ()
    # 安全分析与授权实际绑定的那几份脚本正文. inline -c, heredoc, 临时脚本和脚本文件
    # 都先解析成它再展示 —— 只给路径等于让用户批准一个他没读过的文件.
    script_snapshots: tuple[ScriptSnapshot, ...] = ()
    risk_facts: tuple[str, ...] = ()
    # 目标集合为什么没封闭. 空表示已封闭.
    unresolved_reason: str | None = None
    allowed_scopes: tuple[ApprovalScope, ...] = (ApprovalScope.ONCE,)

    # ---- 从 plan 读出的展示事实 ----

    @property
    def raw_command(self) -> str:
        """要执行的命令原文.

        没有原始命令的工具 (fs.edit_file 之类) 用动作摘要顶上, 但绝不留空: 这一行是
        用户唯一能看懂"要发生什么"的地方.
        """
        subject = self.plan.analysis_subject
        if isinstance(subject, ShellSubject):
            return subject.raw_command
        return self.action_summary

    @property
    def content_previews(self) -> tuple[ContentPreview, ...]:
        """写入目标**将会变成什么**. 只列路径是不够的.

        不进 view_hash: 内容已由 ``plan.normalized_input`` 绑定, 而那一项在
        ``plan_hash`` 里.
        """
        return self.plan.content_previews

    @property
    def cwd(self) -> str:
        return self.plan.execution_context.cwd

    @property
    def target_resolution(self) -> str:
        return self.plan.target_resolution.value

    @property
    def target_set_hash(self) -> str:
        return self.plan.target_set_hash

    @property
    def closed(self) -> bool:
        """目标集合是否封闭. 判据是 plan 自己的 target_resolution, 不是别处的字符串."""
        return self.plan.target_resolution.closed

    @property
    def target_groups(self) -> tuple[TargetGroup, ...]:
        effects = self.plan.effects
        return (
            TargetGroup("读取", effects.read_paths),
            TargetGroup("写入", effects.write_paths),
            TargetGroup("删除", effects.delete_paths),
            TargetGroup(
                "移动",
                tuple(f"{pair.source} -> {pair.target}" for pair in effects.move_pairs),
            ),
            TargetGroup("网络", effects.network_targets),
            TargetGroup("外部副作用", effects.external_effects),
        )

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

    @property
    def view_hash(self) -> str:
        """用户批准的是这一份视图. ApprovalBinding 绑定它, 确认后不能静默扩大范围.

        源字段显式列出而不是 ``digest(self)``: plan 的部分只取 ``plan_hash`` ——
        它已经覆盖工具, spec, 入参, 能力, 效果与目标集合, 再把整个 plan 摊平进来只是
        重算一遍同样的东西.
        """
        return digest(
            {
                "plan_hash": self.plan.plan_hash,
                "action_summary": self.action_summary,
                "user_intent_summary": self.user_intent_summary,
                "workspace_roots": self.workspace_roots,
                "script_snapshots": self.script_snapshots,
                "risk_facts": self.risk_facts,
                "unresolved_reason": self.unresolved_reason,
                "allowed_scopes": self.allowed_scopes,
            }
        )

    def to_payload(self) -> dict[str, object]:
        """交给远程界面的 JSON 形状.

        显式列键, 不反射 dataclass 字段: 反射会把整个 ToolPlan (含 normalized_input
        与 analysis_subject) 一起送出去, 而界面一项都用不上.
        """
        return {
            "mode": self.mode,
            "workspace_roots": list(self.workspace_roots),
            "raw_command": self.raw_command,
            "target_resolution": self.target_resolution,
            "target_groups": [
                {"label": group.label, "paths": list(group.paths)}
                for group in self.target_groups
            ],
            "script_snapshots": [
                {
                    "language": snapshot.language,
                    "origin": snapshot.origin,
                    "path": snapshot.path,
                    "source": snapshot.source,
                }
                for snapshot in self.script_snapshots
            ],
            "content_previews": [
                {
                    "path": preview.path,
                    "content": preview.content,
                    "truncated": preview.truncated,
                }
                for preview in self.content_previews
            ],
            "unresolved_reason": self.unresolved_reason,
            "allowed_scopes": [scope.value for scope in self.allowed_scopes],
        }


@dataclass(frozen=True)
class ApprovalRequest:
    """一次待人类决定的请求. 创建它不等于允许执行, ShellTool 此时绝不能被调用."""

    approval_id: str
    binding: ApprovalBinding
    view: ApprovalView
    mandatory: bool = False

    def __post_init__(self) -> None:
        if not self.approval_id.strip():
            raise ValueError("ApprovalRequest.approval_id 不能为空")
        if self.mandatory and self.view.allowed_scopes != (ApprovalScope.ONCE,):
            # Mandatory Ask 只能创建与本次请求严格绑定的一次性审批: 不支持 session,
            # workspace 或 always (ADR-0013 §4.1).
            raise ValueError("Mandatory Ask 只能使用 once 范围")
        if self.binding.view_hash != self.view.view_hash:
            raise ValueError("ApprovalBinding 必须绑定同一份审批视图")

    def response_error(self, response: ApprovalResponse) -> str | None:
        """验证适配器返回的决定确实属于当前审批请求."""
        if response.approval_id != self.approval_id:
            return "审批响应 id 与当前请求不一致"
        if response.approved and response.scope not in self.view.allowed_scopes:
            return f"审批响应使用了未提供的范围: {response.scope.value}"
        if (
            self.mandatory
            and response.approved
            and response.scope is not ApprovalScope.ONCE
        ):
            return "Mandatory Ask 只能批准本次执行"
        return None


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
