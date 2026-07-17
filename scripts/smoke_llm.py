"""真实调用冒烟脚本：装配网关 -> 发一句「你好」-> 打印回复。

**会真实打网络、产生真实调用费用**，因此不是自动化测试：它住在 scripts/ 而不是
tests/，pytest 的 testpaths 只含 tests/，`make ci` 不会碰它。需要手动运行：

    export DEEPSEEK_API_KEY=sk-xxxxxx
    poetry run python scripts/smoke_llm.py

默认调 deepseek/deepseek-chat；base_url 与 key 的环境变量名都取自代码侧 provider
注册表（providers.REGISTRY），不另立一套。key 只从环境变量读，脚本不接受明文
key 参数，也不打印凭证。
"""

from __future__ import annotations

import sys

from forgecli.application.llm import providers as provider_registry
from forgecli.application.llm.gateway import (
    ChatMessage,
    CurrentModelSelection,
    DefaultLlmGateway,
    DefaultModelSelectionResolver,
    InMemoryModelCatalog,
    ModelCatalogEntry,
    ModelGatewayError,
    ModelParams,
    ModelRequest,
    ProviderRegistry,
    ProviderRuntimeSettings,
    ProviderSettingsSource,
    RequestOrigin,
    TextBlock,
)
from forgecli.application.llm.model_ref import ModelRef
from forgecli.domain.conversation import MessageRole
from forgecli.infrastructure.llm.adapters import OpenAICompatibleProvider
from forgecli.infrastructure.llm.credentials import (
    EnvCredentialResolver,
    InMemoryCredentialPool,
)

_PROVIDER = "deepseek"
_MODEL = "deepseek-v4-flash"
_SPEC = provider_registry.REGISTRY[_PROVIDER]


class _Settings(ProviderSettingsSource):
    """最小设置源：以注册表的 api_key_env 作为唯一凭证引用（即环境变量名）。"""

    def settings_for(self, provider_id: str) -> ProviderRuntimeSettings:
        return ProviderRuntimeSettings(
            provider_id=provider_id,
            timeout_seconds=60.0,
            max_retries=2,
            credential_refs=(_SPEC.api_key_env,),
        )


def _build_gateway() -> DefaultLlmGateway:
    registry = ProviderRegistry()
    registry.register(
        OpenAICompatibleProvider(provider_id=_PROVIDER, base_url=_SPEC.default_api_base)
    )
    catalog = InMemoryModelCatalog(
        (
            ModelCatalogEntry(
                provider=_PROVIDER,
                model=_MODEL,
                context_window=65536,
                max_output_tokens=8192,
            ),
        )
    )
    return DefaultLlmGateway(
        registry,
        resolver=DefaultModelSelectionResolver(
            catalog, current_model=ModelRef(provider=_PROVIDER, model=_MODEL)
        ),
        settings_source=_Settings(),
        credential_pool=InMemoryCredentialPool(EnvCredentialResolver()),
    )


def main() -> int:
    request = ModelRequest(
        request_id="smoke_1",
        session_id="smoke",
        turn_id="turn_0001",
        origin=RequestOrigin.CHAT,
        model_selection=CurrentModelSelection(),
        messages=(ChatMessage(role=MessageRole.USER, content=(TextBlock("你好"),)),),
        params=ModelParams(),
    )
    print(f"-> {_PROVIDER}/{_MODEL} ({_SPEC.default_api_base})，发送「你好」…")
    try:
        response = _build_gateway().complete(request)
    except ModelGatewayError as exc:
        # 网关已把所有 provider 错误归一化；消息是安全摘要，不含凭证。
        print(f"调用失败 [{type(exc).__name__}] {exc}", file=sys.stderr)
        return 1

    usage = response.usage
    print(f"回复: {response.content}")
    print(f"模型: {response.provider}/{response.model}")
    print(
        f"用量: in={usage.input_tokens} out={usage.output_tokens} "
        f"total={usage.total_tokens} estimated={usage.estimated}"
    )
    print(f"耗时: {response.latency_ms:.0f}ms  finish={response.finish_reason.value}")
    if response.raw_metadata:
        print(f"摘要: {dict(response.raw_metadata)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
