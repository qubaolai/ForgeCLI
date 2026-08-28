"""模型选择的解析 (ADR-0011 §2 / §3.4 / §5 运行期装配)。

把当前模型或显式选择解析成具体 provider/model, 并在调用前经 ModelCatalogService
完成存在性与能力校验。依赖方向 (§2, 均只读):

    DefaultLlmGateway -> ConfigBackedSelectionResolver -> ModelCatalogService

语义 (照 ADR §决策 / §3.4):
    - ExplicitModelSelection: 单次显式指定 provider/model, 直接采用,
      **不套用 origin 覆盖、不做模型 fallback**。
    - CurrentModelSelection: origin 命中按用途覆盖则用覆盖模型, 否则用当前主模型;
      两者都不 fallback。未配置当前模型且该 origin 无覆盖 -> ModelBadRequestError。
    - origin 只作用途标签, 不选择模型; 解析不读 TOML, 覆盖表由外部注入。

能力校验只做*静态*比对 (§4): 按 required_capabilities 比对条目的 supports_* 标志、
按 min_context_window 比对 context_window。**不**由 messages 推算 token 数 —— 真实
token 估算是 ApproximateTokenEstimator。

当前模型 (/model)、按用途覆盖 (/config) 与模型目录 (llm.json) 都可能在会话中被改,
因此不能在启动时把它们冻进解析器: ConfigBackedSelectionResolver 每次 resolve 现读
这三个来源, 再交给纯函数 resolve_selection。

ADR-0028: 这里曾经是三个文件 —— 一个纯函数模块, 一个只负责"现读哪三个来源"的类,
以及一个只有那个类实现的 ModelSelectionResolver 抽象。抽象存在的唯一理由是断开
gateway -> 解析器 -> catalog_builder -> gateway 的 import 环; 环的真正成因是
gateway/__init__ 的急切转导出, 而不是这里缺一层间接。成因修掉之后, 抽象没有第二个
实现, 也不跨任何机器守得住的边界, 按规则 A 删除。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType

from forgecli.application.config.config_service import ConfigService
from forgecli.application.llm.catalog import ModelCatalogService
from forgecli.application.llm.catalog_builder import build_catalog
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.application.llm.gateway.errors import (
    ModelBadRequestError,
    ModelContextOverflowError,
)
from forgecli.application.llm.thinking_runtime import ThinkingRuntimeState
from forgecli.domain.model.catalog import ModelCatalogEntry
from forgecli.domain.model.model_ref import ModelRef
from forgecli.domain.model.origin import RequestOrigin
from forgecli.domain.model.resolved import ResolvedModel
from forgecli.domain.model.selection import (
    ExplicitModelSelection,
    ModelSelection,
)
from forgecli.domain.model.thinking import ThinkingMode

# required_capabilities 里的能力字符串 -> ModelCatalogEntry 上对应的 supports_* 标志。
# 这是封闭集合：请求未收录的能力字符串视为无法满足，直接归一为 bad request。
_CAPABILITY_FLAGS: dict[str, str] = {
    "structured_output": "supports_structured_output",
    "tool_calling": "supports_tool_calling",
}

__all__ = ["ConfigBackedSelectionResolver", "resolve_selection"]


class ConfigBackedSelectionResolver:
    """每次 resolve 现读配置（当前模型 / 覆盖表 / 目录）的动态解析器。"""

    def __init__(
        self,
        *,
        config_service: ConfigService,
        llm_config_service: LlmConfigService,
        overrides_loader: Callable[[], Mapping[RequestOrigin, ModelRef]],
        thinking_state: ThinkingRuntimeState | None = None,
    ) -> None:
        self._config = config_service
        self._llm = llm_config_service
        self._load_overrides = overrides_loader
        self._thinking_state = thinking_state

    def resolve(
        self,
        selection: ModelSelection,
        *,
        origin: RequestOrigin,
        required_capabilities: tuple[str, ...] = (),
        min_context_window: int | None = None,
    ) -> ResolvedModel:
        catalog = build_catalog(self._llm.config())
        return resolve_selection(
            selection,
            catalog=catalog,
            current_model=self._config.effective().default_model,
            overrides=self._load_overrides(),
            origin=origin,
            required_capabilities=required_capabilities,
            min_context_window=min_context_window,
            thinking_state=self._thinking_state,
        )


def resolve_selection(
    selection: ModelSelection,
    *,
    catalog: ModelCatalogService,
    current_model: ModelRef | None,
    overrides: Mapping[RequestOrigin, ModelRef] = MappingProxyType({}),
    origin: RequestOrigin,
    required_capabilities: tuple[str, ...] = (),
    min_context_window: int | None = None,
    thinking_state: ThinkingRuntimeState | None = None,
) -> ResolvedModel:
    """当前模型 + 按用途覆盖的只读解析；结果一律经 catalog 校验。"""
    ref = _select_ref(selection, origin, current_model, overrides)
    entry = catalog.get(ref)  # 未知模型 -> ModelBadRequestError
    if thinking_state is not None:
        entry = thinking_state.apply(ref, entry)
    _validate(ref, entry, required_capabilities, min_context_window)
    return ResolvedModel(ref=ref, entry=entry)


def _select_ref(
    selection: ModelSelection,
    origin: RequestOrigin,
    current_model: ModelRef | None,
    overrides: Mapping[RequestOrigin, ModelRef],
) -> ModelRef:
    """按选择类型与 origin 选出目标 ModelRef（不做任何模型 fallback）。"""
    if isinstance(selection, ExplicitModelSelection):
        # 单次显式选择：直接采用，绕过按用途覆盖与当前模型。
        return ModelRef(provider=selection.provider, model=selection.model)

    # CurrentModelSelection：先看该 origin 是否配置了按用途覆盖。
    override = overrides.get(origin)
    if override is not None:
        return override
    if current_model is None:
        raise ModelBadRequestError(
            "未配置当前模型（缺少 [model].provider / [model].model），"
            f"且用途 {origin.value!r} 无按用途覆盖；请用 /model 选择模型。"
        )
    return current_model


def _validate(
    ref: ModelRef,
    entry: ModelCatalogEntry,
    required_capabilities: tuple[str, ...],
    min_context_window: int | None,
) -> None:
    """allowlist / 能力 / 上下文窗口的静态前置校验；任何不满足都归一为网关错误。"""
    if not entry.allowlisted:
        raise ModelBadRequestError(
            f"模型 {ref} 不在允许清单（allowlist）内",
            provider=ref.provider,
            model=ref.model,
        )

    for capability in required_capabilities:
        if capability == "thinking":
            supported = entry.thinking_mode is not ThinkingMode.OFF
            if not supported:
                raise ModelBadRequestError(
                    f"模型 {ref} 不支持所需能力: {capability}",
                    provider=ref.provider,
                    model=ref.model,
                )
            continue
        flag = _CAPABILITY_FLAGS.get(capability)
        if flag is None:
            raise ModelBadRequestError(
                f"未知能力诉求: {capability!r}",
                provider=ref.provider,
                model=ref.model,
            )
        if not getattr(entry, flag):
            raise ModelBadRequestError(
                f"模型 {ref} 不支持所需能力: {capability}",
                provider=ref.provider,
                model=ref.model,
            )

    if min_context_window is not None and entry.context_window < min_context_window:
        raise ModelContextOverflowError(
            f"模型 {ref} 上下文窗口 {entry.context_window} "
            f"小于所需 {min_context_window}",
            provider=ref.provider,
            model=ref.model,
        )
