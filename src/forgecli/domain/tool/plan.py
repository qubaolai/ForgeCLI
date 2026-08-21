"""ToolPlan: 工具系统与安全模块之间唯一的事实载体 (ADR-0004 §4).

两条口径决定了这些字段怎么读:

- **声明只能缩小信任, 不能证明安全.** effects 为空或 declaration_confidence=OPAQUE 不
  等于无副作用, 只表示工具无法自证, 按 ADR-0013 的 UNKNOWN / OPAQUE 路径处理.
- **谁能证明目标集合, 谁就负责冻结它.** fs.* 在 prepare 里给出 STATIC /
  FORGE_EXPANDED 和 target_set_hash; shell.run 给 UNKNOWN 加 analysis_subject, 由安全侧
  的 Shell 分析器冻结后经 effective_plan 回写. 两条路径产出同构证据, 下游不区分冻结
  发生在哪一侧.

与 ADR 字段表的一处合并: ADR 同时列了 expansion_context 与 execution_context_ref, 但
§4 又要求"prepare 的展开必须与执行使用同一份 ExecutionContext". 两者恒等, 拆成两个
字段只会让"展开上下文与执行上下文不一致"这种非法状态变得可表达, 故合并为
execution_context 一个字段.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType

from forgecli.domain.tool.capability import (
    CAPABILITY_VOCABULARY_VERSION,
    Capability,
)
from forgecli.domain.tool.hashing import digest

__all__ = [
    "AnalysisSubject",
    "DeclarationConfidence",
    "ExecutionContextRef",
    "ContentPreview",
    "FileStateBinding",
    "MovePair",
    "PlanEffects",
    "ShellSubject",
    "TargetResolution",
    "ToolPlan",
    "WorkspaceScope",
    "empty_input",
]


class TargetResolution(Enum):
    """目标集合的封闭程度 (口径与 ADR-0013 §6.2 完全一致)."""

    STATIC = "static"  # 目标可从结构化请求直接确定
    FORGE_EXPANDED = "forge_expanded"  # 按固定上下文展开并固定目标集合
    DYNAMIC = "dynamic"  # 目标由 Shell、管道输入或子进程在运行时产生
    UNKNOWN = "unknown"  # 无法可靠推导完整目标集合

    @property
    def closed(self) -> bool:
        """目标集合是否已封闭 —— 只有封闭的目标才可能获得普通 ALLOW 直写真实工作区."""
        return self in (TargetResolution.STATIC, TargetResolution.FORGE_EXPANDED)


class DeclarationConfidence(Enum):
    """effects 的来源可信度."""

    DECLARED = "declared"  # 工具按参数语义直接给出
    DERIVED = "derived"  # 由分析器从原始材料推导
    OPAQUE = "opaque"  # 工具无法自证


class WorkspaceScope(Enum):
    """本次调用触达的位置相对工作区的关系."""

    IN_WORKSPACE = "in_workspace"
    ADDED_DIR = "added_dir"
    OUTSIDE = "outside"


@dataclass(frozen=True)
class MovePair:
    """一次移动 / 重命名的源与目标 (恢复层要同时校验两端)."""

    source: str
    target: str


@dataclass(frozen=True)
class ContentPreview:
    """某个写入目标**将会变成什么**, 供审批界面逐字展示.

    为什么要有这个字段: 审批界面只列路径是不够的. "写入 README.md" 这句话里没有任何
    让人能做判断的信息 —— 用户要看的是内容. 而工具层不能依赖安全模块 (ADR-0004 §13),
    所以内容要经一个中立结构从 ToolPlan 交出来, 不能由审批层去猜 normalized_input 的键.

    它是 normalized_input 的**投影**, 不参与 plan_hash: 内容的绑定已经由
    normalized_input 完成, 这里再算一遍只是重复.
    """

    path: str
    content: str
    truncated: bool = False


@dataclass(frozen=True)
class FileStateBinding:
    """执行必须继续使用的同一份文件输入。

    Shell 分析会读取可执行文件和脚本内容。只把分析结论放在临时 ``findings`` 里，
    等于允许文件在裁决后、执行前被替换。这个结构把身份与内容哈希放回 ToolPlan，
    由统一运行时在启动工具前复核；分析器不再各自实现一套 TOCTOU 检查。
    """

    path: str
    realpath: str
    file_identity: str
    size: int
    mtime_ns: int
    content_hash: str


@dataclass(frozen=True)
class PlanEffects:
    """本次调用声明或推导出的副作用事实. 路径一律为规范化后的绝对路径."""

    read_paths: tuple[str, ...] = ()
    write_paths: tuple[str, ...] = ()
    delete_paths: tuple[str, ...] = ()
    move_pairs: tuple[MovePair, ...] = ()
    network_targets: tuple[str, ...] = ()
    external_effects: tuple[str, ...] = ()
    child_process: bool = False
    dynamic_execution: bool = False

    @property
    def mutating_targets(self) -> tuple[str, ...]:
        """会被改写的目标集合: 写 + 删 + 移动两端. 冲突域与 target_set_hash 都用它."""
        moved = tuple(p for pair in self.move_pairs for p in (pair.source, pair.target))
        return tuple(sorted({*self.write_paths, *self.delete_paths, *moved}))

    @property
    def target_set_hash(self) -> str:
        """已封闭目标集合的哈希. 目标身份变化即旧裁决与审批失效."""
        return digest(self.mutating_targets)


@dataclass(frozen=True)
class AnalysisSubject:
    """供能力分析器深入分析的原始材料 (密封基类).

    结构由**能力**决定而不是由工具决定: 任何声明 EXECUTE_SHELL 的工具都交出
    ShellSubject, 安全模块因此不必认识工具类型.
    """


@dataclass(frozen=True)
class ShellSubject(AnalysisSubject):
    raw_command: str


@dataclass(frozen=True)
class ExecutionContextRef:
    """冻结的执行上下文引用: prepare 的展开与真实执行必须用同一份.

    `filesystem_view_version` 标成 compare=False 且**不进 plan_hash**. 它是每次调用
    新建的观察入口身份 (`fs-{time_ns}`), 不是文件系统内容哈希; 把它算进 plan 身份会让
    plan_hash 永远不重复 —— 而 plan_hash 正是学习规则与风险缓存的匹配键, 于是
    "以后遇到相同命令直接允许"从来没有生效过, 用户每次都会被重新询问.

    它仍然保留在这里: 一次调用内部, prepare 与执行必须是同一份视图, 而这个字段是那件事
    的凭据. 只是它属于"这一次调用的环境", 不属于"这次调用是什么".
    """

    cwd: str
    environment_hash: str
    filesystem_view_version: str = field(default="", compare=False)
    toolchain_id: str = "default"


@dataclass(frozen=True)
class ToolPlan:
    """一次调用的结构化事实. plan_hash 绑定裁决对象与执行对象, 消除 TOCTOU."""

    plan_id: str
    tool_name: str
    spec_hash: str
    normalized_input: Mapping[str, object]
    capabilities: frozenset[Capability]
    effects: PlanEffects
    target_resolution: TargetResolution
    workspace_scope: WorkspaceScope
    execution_context: ExecutionContextRef
    declaration_confidence: DeclarationConfidence = DeclarationConfidence.DECLARED
    analysis_subject: AnalysisSubject | None = None
    capability_vocabulary_version: str = CAPABILITY_VOCABULARY_VERSION
    # 写入内容的展示投影. compare=False: 内容本身已由 normalized_input 绑定, 这里只是
    # 为了让审批界面拿得到它而不必去猜每个工具的入参键名.
    content_previews: tuple[ContentPreview, ...] = field(default=(), compare=False)
    # 安全分析实际读取过的文件。它参与 plan_hash，并在 perform 前统一重验。
    file_state_bindings: tuple[FileStateBinding, ...] = ()
    # 派生字段.
    target_set_hash: str = field(default="", compare=False)
    plan_hash: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        if not self.plan_id.strip():
            raise ValueError("ToolPlan.plan_id 不能为空")
        if not self.capabilities:
            raise ValueError("ToolPlan.capabilities 不能为空")
        object.__setattr__(self, "target_set_hash", self.effects.target_set_hash)
        object.__setattr__(self, "plan_hash", digest(self._hash_source()))

    @property
    def mutates_workspace(self) -> bool:
        """本次调用是否会真实改写工作区 (恢复层据此决定是否建立屏障).

        判据是声明的**写能力**, 不是"有没有推导出写入目标". 差别要紧: `npm test` 与
        `java -jar x.jar` 推不出目标, 但它们能写 —— 分析器要为这种情形声明
        WORKSPACE_WRITE, 恢复层才看得到它们. 反过来也要成立: `ls` 声明了 EXECUTE_SHELL
        却确定不写, 不该因此触发一次全工作区快照, 所以这里不看 EXECUTE_SHELL.
        """
        return bool(self.effects.mutating_targets) or bool(
            self.capabilities
            & {
                Capability.WORKSPACE_WRITE,
                Capability.WORKSPACE_DELETE,
                Capability.PATH_MOVE,
            }
        )

    def _hash_source(self) -> dict[str, object]:
        # plan_id 不参与: 它是这次调用的身份, 不是事实. 两次内容完全相同的调用应当得到
        # 同一个 plan_hash, 否则 ADR-0013 的风险缓存永远命不中.
        return {
            "tool_name": self.tool_name,
            "spec_hash": self.spec_hash,
            "capability_vocabulary_version": self.capability_vocabulary_version,
            "normalized_input": dict(self.normalized_input),
            "capabilities": self.capabilities,
            "effects": self.effects,
            "target_resolution": self.target_resolution,
            "target_set_hash": self.effects.target_set_hash,
            "workspace_scope": self.workspace_scope,
            "declaration_confidence": self.declaration_confidence,
            "analysis_subject": self.analysis_subject,
            "execution_context": self.execution_context,
            "file_state_bindings": self.file_state_bindings,
        }


def empty_input() -> Mapping[str, object]:
    """空入参的共享只读映射 (dataclass 默认值不能直接用可变 dict)."""
    return MappingProxyType({})
