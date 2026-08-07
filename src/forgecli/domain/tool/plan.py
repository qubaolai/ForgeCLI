"""工具系统与安全模块之间的唯一桥梁

两条口径决定这些字段如何读取:

1. 声明只能缩小信任, 不能证明安全; effects 为空或 declaration_confidence=OPAQUE 不等于安全,
   只表示工具无法自证, 按 ADR-0013 的 UNKNOWN / OPAQUE 路径处理.
2. 谁能证明目标集合, 谁就负责冻结它. fs.* 在prepare 里给出 STATIC / FORGE_EXPANDED 
   和 target_set_hash; shell.run 给 UNKNOWN 加 analysis_subject, 由安全侧
   的 Shell 分析器冻结后经 effective_plan 回写. 两条路径产出同构证据, 下游不区分冻结
   发生在哪一侧.


"""

from __future__ import annotations
from abc import ABC
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from turtle import st
from types import MappingProxyType

from forgecli.domain.tool import spec
from forgecli.domain.tool.capability import CAPABILITY_VOCABULARY_VERSION, Capability
from forgecli.domain.tool.hashing import digest

__all__ = [
    "AnalysisSubject",
    "DeclarationConfidence",
    "ExecutionContextRef",
    "MovePair",
    "NetworkSubject",
    "PlanEffects",
    "ScriptSubject",
    "ShellSubject",
    "TargetResolution",
    "ToolPlan",
    "WorkspaceScope",
    "empty_input",
]

class TargetResolution(Enum):
    """目标集合的封闭程度 (ADR-0013 §6.2)"""

    STATIC = "static"
    FORGE_EXPANDED = "forge_expanded"
    DYNAMIC = "dynamic"
    UNKNOWN = "unknown"

    @property
    def closed(self) -> bool:
        """目标集合是否已封闭, 只有封闭的木匾才能获得普通 ALLOW 直接写真是工作区"""
        return self in (TargetResolution.STATIC, TargetResolution.FORGE_EXPANDED)


class DeclarationConfidence(Enum):
    """effects 的来源可信度"""

    DECLARED = "declared"  # 工具按参数语义直接给出
    DERIVED = "derived"  # 由分析器从原始材料推导
    OPAQUE = "opaque"  # 工具无法自证


class WorkspaceScope(Enum):
    """本次调用触达的位置相对工作区的关系."""

    IN_WORKSPACE = "in_workspace"
    ADDED_DIR = "added_dir"
    OUTSIDE = "outside"

# TODO 应该可删除
@dataclass(frozen=True)
class MovePair:
    """一次移动 / 重命名的源与目标 (恢复层要同时校验两端)."""

    source: str
    target: str


@dataclass(frozen=True)
class PlanEffects:
    """本次调用生命活着推导出的内容, 路径一路为规范化后的绝对路径"""

    read_path: tuple[str, ...] = ()
    write_path: tuple[str, ...] = ()
    delete_path: tuple[str, ...] = ()
    move_pairs: tuple[MovePair, ...] = ()
    network_targets: tuple[str, ...] = ()
    external_effects: tuple[str, ...] = ()
    child_process: bool = False
    dynamic_execution: bool = False

    @property
    def mutating_targets(self) -> tuple[str, ...]:
        """会被修改的目标集合: 写 | 删除 | 移动 | 冲突域 | target_set_hash 都用它"""
        moved = tuple(p for pair in self.move_pairs for p in (pair.source, pair.target))
        return tuple(sorted({*self.write_path, *self.delete_path, *moved}))

    @property
    def target_set_hash(self) -> str:
        """已封闭目标集合的哈希. 目标身份变化即代表旧的裁决与审批失效"""
        return digest(self.mutating_targets)


@dataclass(frozen=True)
class AnalysisSubject(ABC):
    """供能力分析器深入分析的基类.

    结构由能力决定而不是由工具决定: 任何声明 EXECUTE_SHELL 的工具都交出
    ShellSubject, 安全模块因此不必认识工具类型.
    """


@dataclass(frozen=True)
class ShellSubject(AnalysisSubject):
    shell_kind: str
    raw_command: str
    cwd: str
    env_snapshot_ref: str


@dataclass(frozen=True)
class ScriptSubject(AnalysisSubject):
    language: str
    script_source: str | None = None
    script_path: str | None = None
    entry_config_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.script_source is None and self.script_path is None:
            raise ValueError("ScriptSubject 必须给出 script_source 或 script_path")


@dataclass(frozen=True)
class NetworkSubject(AnalysisSubject):
    url_or_host: str
    method: str = "post"
    credential_scope_ref: str | None = None


@dataclass(frozen=True)
class ExecutionContextRef:
    """固定的执行上下文引用: prepare 的展开与真实执行时必须使用同一个上下文"""

    cwd: str
    environment_hash: str
    filesystem_view_version: str
    toolchain_id: str = "default"


@dataclass(frozen=True)
class ToolPlan:
    """一次工具调用的结构化信息. plan_hash 绑定裁决对象与执行对象, 消除TOCTOU"""

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
    # 派生字段
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
        """本次调用是否会真是改写工作区"""
        return bool(self.effects.mutating_targets) or bool(
            self.capabilities
            & {
                Capability.WORKSPACE_WRITE,
                Capability.WORKSPACE_DELETE,
                Capability.PATH_MOVE
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
        }


def empty_input() -> Mapping[str, object]:
    """空入参的共享只读映射 (dataclass 默认值不能直接用可变 dict)."""
    return MappingProxyType({})