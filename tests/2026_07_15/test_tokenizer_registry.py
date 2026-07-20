"""TokenizerRegistry 与可选精确分词器（ADR-0012 §6）。

覆盖：有映射用精确值、最长前缀优先、无映射回落近似、可选依赖缺失（ImportError）
静默回落且不影响导入、gateway 经 registry 取估算器。
"""

from __future__ import annotations

from forgecli.application.llm.gateway import (
    ApproximateTokenEstimator,
    ChatMessage,
    CurrentModelSelection,
    DefaultLlmGateway,
    DefaultModelSelectionResolver,
    InMemoryModelCatalog,
    ModelCatalogEntry,
    ModelParams,
    ModelRequest,
    ProviderRegistry,
    RequestOrigin,
    TextBlock,
    TokenEstimator,
    TokenizerRegistry,
)
from forgecli.application.llm.gateway.messages import ToolSpec
from forgecli.application.llm.model_ref import ModelRef
from forgecli.domain.conversation import MessageRole
from forgecli.infrastructure.llm.adapters import FakeModelProvider

_REF = ModelRef(provider="deepseek", model="deepseek-chat")


class _FixedEstimator(TokenEstimator):
    """确定性假精确分词器：输入/文本都返回固定值。"""

    def __init__(self, value: int) -> None:
        self.value = value

    def estimate_input(
        self,
        *,
        messages: tuple[ChatMessage, ...],
        system_prompt: str | None = None,
        tools: tuple[ToolSpec, ...] = (),
    ) -> int:
        return self.value

    def estimate_text(self, text: str) -> int:
        return self.value


def test_registered_tokenizer_used_for_matching_model() -> None:
    registry = TokenizerRegistry()
    registry.register("deepseek", "deepseek-", lambda: _FixedEstimator(7))
    estimator = registry.estimator_for(_REF)
    assert isinstance(estimator, _FixedEstimator)
    assert estimator.estimate_text("anything") == 7


def test_longest_prefix_wins() -> None:
    registry = TokenizerRegistry()
    registry.register("deepseek", "", lambda: _FixedEstimator(1))
    registry.register("deepseek", "deepseek-chat", lambda: _FixedEstimator(2))
    registry.register("deepseek", "deepseek-", lambda: _FixedEstimator(3))
    estimator = registry.estimator_for(_REF)
    assert isinstance(estimator, _FixedEstimator)
    assert estimator.value == 2


def test_no_mapping_falls_back_to_approximate() -> None:
    registry = TokenizerRegistry()
    registry.register("openai", "gpt-", lambda: _FixedEstimator(9))
    assert isinstance(registry.estimator_for(_REF), ApproximateTokenEstimator)


def _import_error_factory() -> TokenEstimator:
    raise ImportError("可选依赖 tokenizers 未安装")


def test_import_error_falls_back_silently() -> None:
    registry = TokenizerRegistry()
    registry.register("deepseek", "deepseek-", _import_error_factory)
    estimator = registry.estimator_for(_REF)
    assert isinstance(estimator, ApproximateTokenEstimator)
    # 回落结果被缓存：再次取值仍然回落且不再触发工厂。
    assert registry.estimator_for(_REF) is estimator


def test_gateway_uses_registry_estimator_for_resolved_ref() -> None:
    provider = FakeModelProvider(content="ok")  # 不带 usage -> gateway 估算
    registry = ProviderRegistry()
    registry.register(provider)
    catalog = InMemoryModelCatalog(
        (
            ModelCatalogEntry(
                provider="deepseek", model="deepseek-chat", context_window=65536
            ),
        )
    )
    tokenizers = TokenizerRegistry()
    tokenizers.register("deepseek", "deepseek-", lambda: _FixedEstimator(7))
    gw = DefaultLlmGateway(
        registry,
        resolver=DefaultModelSelectionResolver(catalog, current_model=_REF),
        tokenizer_registry=tokenizers,
    )
    response = gw.complete(
        ModelRequest(
            request_id="req_1",
            session_id="sess_1",
            turn_id="turn_0001",
            origin=RequestOrigin.CHAT,
            model_selection=CurrentModelSelection(),
            messages=(ChatMessage(role=MessageRole.USER, content=(TextBlock("hi"),)),),
            params=ModelParams(),
        )
    )
    # 估算 usage 出自注入的精确估算器（输入 7 / 输出 7）。
    assert response.usage.estimated is True
    assert response.usage.input_tokens == 7
    assert response.usage.output_tokens == 7
