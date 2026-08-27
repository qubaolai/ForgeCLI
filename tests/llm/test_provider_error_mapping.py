"""400 的分流 (ADR-0011 §12).

OpenAI 兼容协议在 400 上没有约定错误码, 所有 4xx 曾经一律归成 ModelBadRequestError ——
包括"提示词太长". 那一条**有救**: 压一次上下文再发就能过, 而按普通坏请求处理会让一轮
跑了几十步的工作整个作废.
"""

from __future__ import annotations

import httpx
import pytest

from forgecli.application.llm.gateway.errors import (
    ModelBadRequestError,
    ModelContextOverflowError,
)
from forgecli.infrastructure.llm.adapters.openai_compatible import (
    OpenAICompatibleProvider,
)


def _raise(body: str, status: int = 400) -> None:
    provider = OpenAICompatibleProvider(
        provider_id="test", base_url="https://example.invalid/v1/chat/completions"
    )
    response = httpx.Response(
        status,
        text=body,
        request=httpx.Request("POST", "https://example.invalid/v1/chat/completions"),
    )
    provider._raise_http_error(status, response)


@pytest.mark.parametrize(
    "body",
    [
        # GLM: 错误码 1261, 措辞是这一句.
        '{"error":{"code":"1261","message":"Prompt exceeds max length"}}',
        '{"error":{"code":"context_length_exceeded",'
        '"message":"maximum context length is 128000 tokens"}}',
        '{"error":{"message":"prompt is too long: 210000 tokens"}}',
    ],
)
def test_a_prompt_too_long_400_is_recoverable(body: str) -> None:
    with pytest.raises(ModelContextOverflowError):
        _raise(body)


@pytest.mark.parametrize(
    "body",
    [
        '{"error":{"message":"Invalid value for temperature"}}',
        '{"error":{"message":"tools[0].function.name is invalid"}}',
    ],
)
def test_other_400s_stay_bad_requests(body: str) -> None:
    """措辞对不上就别猜: 一个真的构造错误被当成"压一压再来", 只会白压一次再失败."""
    with pytest.raises(ModelBadRequestError):
        _raise(body)
