"""统一错误类型的基本行为与分层（ADR-0011 §12）。

要点：所有调用错误归一到 ModelGatewayError 分支；与 config 层的 ConfigError /
UnknownProvider / InvalidModelRef 分属不同分支，命名不混用。
"""

from __future__ import annotations

from forgecli.application.llm.errors import InvalidModelRef, UnknownProvider
from forgecli.application.llm.gateway import (
    ModelAuthError,
    ModelBadRequestError,
    ModelBudgetExceededError,
    ModelCancelledError,
    ModelContextOverflowError,
    ModelGatewayError,
    ModelProviderInternalError,
    ModelRateLimitError,
    ModelResponseParseError,
    ModelTimeoutError,
    ModelUnavailableError,
)
from forgecli.shared.errors import ConfigError, ForgeError

_ALL_GATEWAY_ERRORS = [
    ModelUnavailableError,
    ModelAuthError,
    ModelRateLimitError,
    ModelTimeoutError,
    ModelContextOverflowError,
    ModelBadRequestError,
    ModelProviderInternalError,
    ModelResponseParseError,
    ModelBudgetExceededError,
    ModelCancelledError,
]


def test_all_gateway_errors_share_base_and_forge_root() -> None:
    for err_cls in _ALL_GATEWAY_ERRORS:
        assert issubclass(err_cls, ModelGatewayError)
        assert issubclass(err_cls, ForgeError)


def test_gateway_errors_are_not_config_errors() -> None:
    # 分层清晰：调用错误不混入 config 层。
    assert not issubclass(ModelGatewayError, ConfigError)
    for err_cls in _ALL_GATEWAY_ERRORS:
        assert not issubclass(err_cls, ConfigError)


def test_config_errors_are_not_gateway_errors() -> None:
    assert not issubclass(UnknownProvider, ModelGatewayError)
    assert not issubclass(InvalidModelRef, ModelGatewayError)


def test_base_carries_safe_context() -> None:
    err = ModelAuthError(
        "认证失败",
        provider="deepseek",
        model="deepseek-chat",
        request_id="req_1",
    )
    assert err.message == "认证失败"
    assert (err.provider, err.model, err.request_id) == (
        "deepseek",
        "deepseek-chat",
        "req_1",
    )
    # 上下文默认可空，便于在尚未解析 provider/model 时构造。
    assert ModelTimeoutError("超时").provider is None


def test_rate_limit_carries_retry_after() -> None:
    err = ModelRateLimitError("429", provider="deepseek", retry_after=1.5)
    assert err.retry_after == 1.5
    assert ModelRateLimitError("429").retry_after is None
