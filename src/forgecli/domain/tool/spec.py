"""ToolSpec: 工具的能力上界与机制参数 (ADR-0004 §3).

三条必须记住的口径:

1. **spec 声明的是上界, 不是放行证明.** "spec 里只写了 WORKSPACE_READ"不构成本次调用
   安全的证据 —— 本次调用的事实以 ToolPlan 为准. 上界只用于目录过滤和完整性校验.
2. **没有 requires_authorization.** 是否需要授权不由工具作者, MCP server 或配置决定;
   所有执行统一走 ToolRuntime 的强制授权前置. 真正不需要裁决的纯函数不该注册为工具.
3. **没有 risk_level.** 静态风险等级表达不了"同一个工具读工作区文件与读凭证文件"的
   差异, 留着它会诱导安全模块按等级而不是按事实裁决.
4. **没有 action.** 原先有一个 `ToolAction` 枚举, 用途只有一个: 让提示词里的工具表按
   动作分组. ADR-0042 删掉那张表之后它的消费方归零 —— tool schema 已经带了 name,
   description 与 parameters, 那就是模型选工具的全部依据, 再叠一层 Forge 自造的分类,
   等于要求模型先学会我们的词汇表才能用我们的工具. 而它的成本不是零: 每加一个工具都要
   在一张自己不消费的分类表里选一格, 选错还不报错 (ADR-0028 规则 C).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType

from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.hashing import digest
from forgecli.domain.tool.plan import TargetResolution
from forgecli.domain.tool.tool_call import ToolSchema

__all__ = [
    "ArtifactPolicy",
    "TargetDeclarationAbility",
    "ToolSpec",
]

# 命名空间化的稳定名: fs_read / mcp_github_search_issues. 至少两段, 全小写, 下划线分段.
#
# 分隔符从点号换成下划线 (ADR-0036): OpenAI 的 function name 只收 [A-Za-z0-9_-],
# 带点的名字在部分兼容端点上会被整条请求拒掉.
#
# 仍然要求**至少两段**: 名字要自带归属 (fs / git / shell / mcp_<server>), 否则一个
# 叫 run 的工具与另一个叫 read 的工具放在同一张表里, 模型分不出谁管什么.
# 段内不允许空段, 所以 `fs__read` 与 `fs_read_` 都不合法.
_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)+$")


class TargetDeclarationAbility(Enum):
    """该工具能否在 prepare 里封闭目标集合 (ADR-0004 §4).

    这是 `ToolPlan.target_resolution` 的**上界**: 声明 STATIC 等于承诺"我每次都能从入参
    直接算出确定的目标集合". 承诺兑现不了时必须挡下 —— 一个声明 STATIC 却返回 UNKNOWN
    的工具, 会让下游按"目标已封闭"去做快速裁决, 而实际上根本没封闭.
    """

    STATIC = "static"
    EXPANDABLE = "expandable"
    OPAQUE = "opaque"

    def permits(self, resolution: TargetResolution) -> bool:
        """本次调用的封闭程度是否在声明的上界之内 (比声明更强总是允许的)."""
        return resolution in _PERMITTED_RESOLUTIONS[self]


# 声明 STATIC 的工具只能给出 STATIC; 声明 EXPANDABLE 的可以更强 (STATIC) 但不能更弱.
_PERMITTED_RESOLUTIONS: dict[TargetDeclarationAbility, frozenset[TargetResolution]] = {
    TargetDeclarationAbility.STATIC: frozenset({TargetResolution.STATIC}),
    TargetDeclarationAbility.EXPANDABLE: frozenset(
        {TargetResolution.STATIC, TargetResolution.FORGE_EXPANDED}
    ),
    TargetDeclarationAbility.OPAQUE: frozenset(TargetResolution),
}


@dataclass(frozen=True)
class ArtifactPolicy:
    """输出溢写阈值: 超过 max_inline_bytes 的内容落 artifact, 回填只留引用."""

    # 8 KiB ≈ 2k token. 这个数按**上下文经济**定, 不按执行资源 (ADR-0041 决策 6):
    # 它回答的是"这条正文值不值得在往后每一轮都重发一遍", 而不是"一次调用能占多少内存".
    max_inline_bytes: int = 8 * 1024
    max_artifact_bytes: int = 8 * 1024 * 1024

    def __post_init__(self) -> None:
        if self.max_inline_bytes <= 0:
            raise ValueError("ArtifactPolicy.max_inline_bytes 必须为正")
        if self.max_artifact_bytes < self.max_inline_bytes:
            raise ValueError("max_artifact_bytes 不能小于 max_inline_bytes")


@dataclass(frozen=True)
class ToolSpec:
    """一个工具的完整规格. spec_hash 由全部字段派生, 是跨模块契约锚点."""

    name: str
    version: str
    title: str
    description: str
    input_schema: Mapping[str, object]
    output_schema: Mapping[str, object]
    declared_capabilities: frozenset[Capability]
    target_declaration_ability: TargetDeclarationAbility
    default_timeout_seconds: float
    artifact_policy: ArtifactPolicy = field(default_factory=ArtifactPolicy)
    # 这个工具的结果正文进不进会话窗口 (ADR-0041 决策 6).
    #
    # 默认 False: 窗口增长的主项就是正文, 而绝大多数结果的判据已经在 summary 与 data 里.
    # 只有"改代码之前必须看到原文"这一类才置 True —— 那不是通例, 是例外.
    body_in_window: bool = False
    # 派生字段: 不由调用方传入, __post_init__ 算好后写入.
    spec_hash: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        if not _NAME_PATTERN.match(self.name):
            raise ValueError(f"ToolSpec.name 必须是命名空间化的稳定名: {self.name!r}")
        if not self.version.strip():
            raise ValueError("ToolSpec.version 不能为空")
        if not self.declared_capabilities:
            raise ValueError("ToolSpec.declared_capabilities 不能为空")
        if self.default_timeout_seconds <= 0:
            raise ValueError("ToolSpec.default_timeout_seconds 必须为正")
        object.__setattr__(self, "spec_hash", digest(self._hash_source()))

    def to_model_schema(self) -> ToolSchema:
        """派生发给模型的声明. 单向: 模型侧看不到能力, 上界和信任区."""
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters=MappingProxyType(dict(self.input_schema)),
        )

    def exceeds_upper_bound(self, capabilities: frozenset[Capability]) -> bool:
        """本次调用请求的能力是否超出 spec 上界 (超出即 fail closed)."""
        return not capabilities <= self.declared_capabilities

    def _hash_source(self) -> dict[str, object]:
        """参与 spec_hash 的源字段. 显式列出, 避免把派生字段绕回自身."""
        return {
            "name": self.name,
            "version": self.version,
            "title": self.title,
            "description": self.description,
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema),
            "declared_capabilities": self.declared_capabilities,
            "target_declaration_ability": self.target_declaration_ability,
            "default_timeout_seconds": self.default_timeout_seconds,
            "artifact_policy": self.artifact_policy,
            "body_in_window": self.body_in_window,
        }
