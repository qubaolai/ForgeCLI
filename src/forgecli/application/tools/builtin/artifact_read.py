"""artifact_read: 取回一段已经归档的工具输出 (ADR-0032 决策 6.2).

**这是一级降级唯一的取回路径.** 降级把 transcript 里的正文换成一行引用, 而引用取不
回来的话, 压缩干的事就是"把内容删掉, 再告诉模型它在一个够不着的地方".

模型够不着那些文件: ``build_protected_path_policy`` 把 ``state_dir()`` 注册成
``FORGE_STATE`` 且 ``deny_read=True``, 于是 ``fs_read ~/.forge/state/artifacts/...``
与 ``shell_run cat ...`` 都会走 ``HARD_DENY_CREDENTIAL_ACCESS`` —— 不是 ASK, 既不能
批准也不能用 ``/add-dir`` 豁免.

形状照 ``plan_read`` 抄, 连同它那条理由: ``additionalProperties: False`` 加上
**没有路径字段**, 构成这个工具目标集合在机制上封闭的证明. 所以 ``ToolPlan`` 不声明
任何路径 target, ``workspace_analyzer`` 也就没有东西可判 —— 这不是绕过上面那道
Hard Deny, 是根本不产生受它管辖的路径.

那个证明成立的前提是 **id 不能表达路径**, 由 ``valid_artifact_id`` 守住: 入参到了
这一层是模型可控的, 而 ``_path_of`` 会拿 ``artifact_id[:2]`` 当目录名.
"""

from __future__ import annotations

from types import MappingProxyType

from forgecli.application.prompt.template_renderer import render_notice
from forgecli.application.tools.artifact_store import (
    ArtifactMissing,
    ArtifactStore,
    valid_artifact_id,
)
from forgecli.application.tools.builtin.base import validate_arguments
from forgecli.application.tools.resource_governor import ResourceGovernor
from forgecli.application.tools.tool import Tool, ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.errors import PreparationError, PreparationErrorCode
from forgecli.domain.tool.plan import (
    DeclarationConfidence,
    PlanEffects,
    TargetResolution,
    ToolPlan,
    WorkspaceScope,
)
from forgecli.domain.tool.result import (
    ContentPart,
    ResultProvenance,
    ToolError,
    ToolMetrics,
    ToolResult,
    ToolResultStatus,
)
from forgecli.domain.tool.spec import (
    TargetDeclarationAbility,
    ToolSpec,
)
from forgecli.shared.cancellation import CancelToken

__all__ = ["ArtifactReadTool"]

_SPEC = ToolSpec(
    name="artifact_read",
    version="1",
    title="读回已归档的输出",
    description=(
        "取回一段之前被归档的工具输出. "
        "对话里出现 [已归档 <id>] 这样的引用时, 用这里的 id 取回完整内容. "
        "内容很长时用 offset (从第几行开始, 从 1 起) 与 limit (读多少行) 分段取."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "artifact_id": {"type": "string"},
            "offset": {"type": "integer", "minimum": 1},
            "limit": {"type": "integer", "minimum": 1},
        },
        "required": ["artifact_id"],
        # 没有路径字段, 且不许多带 —— 见模块注释里那条"机制上封闭"的证明.
        "additionalProperties": False,
    },
    output_schema={"type": "object", "properties": {"text": {"type": "string"}}},
    declared_capabilities=frozenset({Capability.ARTIFACT_READ}),
    target_declaration_ability=TargetDeclarationAbility.STATIC,
    default_timeout_seconds=5.0,
    # 它**就是**取回路径, 所以正文必须进窗口 (ADR-0041 决策 6 的例外).
    #
    # 不置 True 的话: 结果自带一个指向同一份归档的句柄, 于是模型读到"内容已归档 X,
    # 用 artifact_read 取回"—— 而它刚做的就是这件事. 一个自指的循环.
    body_in_window=True,
)


class ArtifactReadTool(Tool):
    def __init__(self, governor: ResourceGovernor, artifacts: ArtifactStore) -> None:
        self._governor = governor
        # 不是可选依赖: 没有存储的 artifact_read 只能永远回一句"取不到", 而模型会
        # 一直看到 transcript 里的归档引用. 装不出来就别注册这个工具.
        self._artifacts = artifacts

    @property
    def spec(self) -> ToolSpec:
        return _SPEC

    def prepare(
        self, request: ToolInvocationRequest, context: ExecutionContext
    ) -> ToolPlan | PreparationError:
        invalid = validate_arguments(_SPEC, request.arguments)
        if invalid is not None:
            return invalid
        artifact_id = str(request.arguments.get("artifact_id", ""))
        if not valid_artifact_id(artifact_id):
            # 在这里就拦掉, 而不是等到 _path_of: 早一层给的是可读的入参错误, 晚一层
            # 给的是"找不到", 而后者会让模型以为内容被回收了, 于是放弃取回.
            return PreparationError(
                code=PreparationErrorCode.INVALID_INPUT,
                message=render_notice("context.artifact_bad_id"),
                field_path="artifact_id",
            )
        return ToolPlan(
            plan_id=request.invocation_id,
            tool_name=_SPEC.name,
            spec_hash=_SPEC.spec_hash,
            normalized_input=MappingProxyType(
                {
                    "artifact_id": artifact_id,
                    "offset": request.arguments.get("offset"),
                    "limit": request.arguments.get("limit"),
                }
            ),
            capabilities=frozenset({Capability.ARTIFACT_READ}),
            # 空 effects 是事实, 不是省略: 读的位置由内容哈希算出, 模型指定不了.
            effects=PlanEffects(),
            target_resolution=TargetResolution.STATIC,
            workspace_scope=WorkspaceScope.IN_WORKSPACE,
            execution_context=context.to_ref(),
            declaration_confidence=DeclarationConfidence.DECLARED,
        )

    def perform(
        self,
        plan: ToolPlan,
        context: ExecutionContext,
        cancel: CancelToken | None = None,
    ) -> ToolResult:
        artifact_id = str(plan.normalized_input["artifact_id"])
        try:
            body = self._artifacts.read(
                artifact_id,
                offset=_positive(plan.normalized_input.get("offset")),
                limit=_positive(plan.normalized_input.get("limit")),
            )
        except ArtifactMissing:
            # 3 天过期回收之后仍然指着它的引用会走到这里 (决策 6.1). 说清是"回收了"
            # 而不是"读失败", 否则模型会一直重试同一个 id.
            return ToolResult(
                invocation_id=plan.plan_id,
                tool_name=_SPEC.name,
                status=ToolResultStatus.TOOL_ERROR,
                summary=f"归档 {artifact_id} 已过期回收, 取不回来了",
                data={"artifact_id": artifact_id},
                content_parts=(
                    ContentPart(text=render_notice("context.artifact_missing")),
                ),
                error=ToolError(
                    code="artifact_expired",
                    message=render_notice("context.artifact_missing"),
                    retryable=False,
                ),
            )
        limits = self._governor.limits_for(_SPEC)
        inline, truncated = ResourceGovernor.clamp(body, limits.max_inline_bytes)
        # 不走 emit_text: 那会把取回的内容再归档一遍. 超长时给的是分段取的办法,
        # 而不是又一个 id —— 内容本来就已经在同一个 id 底下.
        text = (
            f"{inline}\n{render_notice("context.artifact_window_truncated")}"
            if truncated
            else inline
        )
        return ToolResult(
            invocation_id=plan.plan_id,
            tool_name=_SPEC.name,
            status=ToolResultStatus.OK,
            summary=f"取回归档 {artifact_id}, {len(text.splitlines())} 行",
            data={"artifact_id": artifact_id, "lines": len(text.splitlines())},
            content_parts=(ContentPart(text=text),),
            metrics=ToolMetrics(bytes_out=len(body.encode("utf-8"))),
            # 取回来的这一条本身也能再被降级, 而且降回同一个 id —— 内容寻址让这件事
            # 不占额外空间.
            provenance=ResultProvenance(
                artifact_id=artifact_id, byte_size=len(body.encode("utf-8"))
            ),
        )


def _positive(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
