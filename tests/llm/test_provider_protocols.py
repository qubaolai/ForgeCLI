"""供应商协议与自建供应商.

Forge 只有一个 adapter (`openai_compatible`). 所以"自主添加供应商"能成立的范围也就只有
讲那一套协议的端点 —— 另外两种要出现在界面上, 但**不可选**.

为什么不干脆不列: 用户看到"暂不支持"与根本看不到这一项是两回事. 后者会让人以为 Forge
不打算支持, 于是去找别的工具.
"""

from __future__ import annotations

import pytest

from forgecli.application.llm import providers as registry
from forgecli.application.llm.config.llm_config_service import LlmConfigService
from forgecli.domain.model.provider_spec import ProviderProtocol
from forgecli.shared.errors import ConfigValidationError


@pytest.fixture(autouse=True)
def _clean_registry():
    registry.forget_user_providers()
    yield
    registry.forget_user_providers()


def test_only_the_openai_compatible_protocol_is_supported() -> None:
    """另外两种是**声明过的未来**, 不是占位: 它们要能被列出来并标成不可选."""
    supported = [item for item in ProviderProtocol if item.supported]

    assert supported == [ProviderProtocol.OPENAI_COMPATIBLE]
    assert len(list(ProviderProtocol)) == 3
    assert all(item.label for item in ProviderProtocol)


def _service(tmp_path) -> LlmConfigService:
    from forgecli.infrastructure.llm.json_store import JsonLlmConfigStore

    return LlmConfigService(JsonLlmConfigStore(tmp_path / "llm.json"))


def test_a_user_provider_becomes_known_and_survives_a_reload(tmp_path) -> None:
    """登记住在进程内存里, 配置文件却是持久的.

    少了"每次读配置重新登记"这一步, 用户加的供应商能写进文件, 重启之后
    `require_known_provider` 却说它不存在 —— 而那时模型已经配在它下面了.
    """
    service = _service(tmp_path)
    service.add_provider(
        "acme",
        name="Acme",
        api_base="https://acme.test/v1/chat/completions",
        protocol="openai_compatible",
        api_key_env="ACME_API_KEY",
    )

    assert registry.is_known_provider("acme")

    # 换一个服务实例 = 进程重启; 登记表是空的, 只剩配置文件.
    registry.forget_user_providers()
    assert not registry.is_known_provider("acme")

    reloaded = _service(tmp_path)
    reloaded.config()

    assert registry.is_known_provider("acme")
    assert registry.require_known_provider("acme").label == "Acme"


def test_an_unsupported_protocol_is_refused_at_the_door(tmp_path) -> None:
    """存进配置只会把失败推迟到第一次真正调用, 那时用户已经把模型也配好了."""
    with pytest.raises(ConfigValidationError, match="Anthropic Messages"):
        _service(tmp_path).add_provider(
            "claude-direct",
            name="Claude",
            api_base="https://api.anthropic.com/v1/messages",
            protocol="anthropic_messages",
        )

    assert not registry.is_known_provider("claude-direct")


def test_an_unknown_protocol_name_reads_differently_from_an_unsupported_one(
    tmp_path,
) -> None:
    """ "不认识这个名字"和"认识但还不支持"要给出不同的提示.

    后者是用户看着界面上那一项选的, 告诉他"未知协议"只会让他以为自己填错了.
    """
    with pytest.raises(ConfigValidationError, match="未知协议"):
        _service(tmp_path).add_provider(
            "x", name="X", api_base="https://x.test", protocol="grpc"
        )


def test_a_builtin_provider_cannot_be_redefined(tmp_path) -> None:
    """内置那几家的默认端点是代码事实; 要改端点走 api_base, 不是重新定义它是什么."""
    with pytest.raises(ConfigValidationError, match="内置供应商"):
        _service(tmp_path).add_provider(
            "glm",
            name="我的 GLM",
            api_base="https://x.test",
            protocol="openai_compatible",
        )


def test_a_user_provider_shows_up_in_the_settings_listing(tmp_path) -> None:
    """内置的没配过也要有一行; 自建的只有配过才存在, 它没有默认值可以回落."""
    service = _service(tmp_path)
    service.add_provider(
        "acme",
        name="Acme",
        api_base="https://acme.test",
        protocol="openai_compatible",
    )

    listed = {item.id for item in service.effective_providers()}

    assert "acme" in listed
    assert "glm" in listed, "内置的仍然全都在"


def test_a_user_provider_without_its_env_var_is_not_reported_ready(
    tmp_path, monkeypatch
) -> None:
    """界面据此显示"密钥已就绪 / 缺少 XXX / 无需密钥".

    实测故障: 自建供应商不在 REGISTRY 里, 于是 `known_providers` 根本不发它们, 页面只能
    自己编一个可用性 —— 编出来的那个一律是"已就绪", 于是一家根本没配环境变量的供应商
    看起来是好的, 直到第一次真正调用才失败。
    """
    from forgecli.application.llm.availability import EnvProviderAvailability

    monkeypatch.delenv("ACME_API_KEY", raising=False)
    _service(tmp_path).add_provider(
        "acme",
        name="Acme",
        api_base="https://acme.test",
        protocol="openai_compatible",
        api_key_env="ACME_API_KEY",
    )

    availability = EnvProviderAvailability()
    assert availability.is_available("acme") is False

    monkeypatch.setenv("ACME_API_KEY", "sk-real")
    assert availability.is_available("acme") is True


def test_a_keyless_endpoint_is_not_reported_ready_either(tmp_path) -> None:
    """免密钥的本地端点走的是另一句话 ("无需密钥"), 不是"就绪".

    两者在界面上要分得开: 前者说的是"这家不需要配", 后者说的是"你配好了".
    """
    from forgecli.application.llm.availability import EnvProviderAvailability

    _service(tmp_path).add_provider(
        "ollama",
        name="Ollama",
        api_base="http://127.0.0.1:11434/v1/chat/completions",
        protocol="openai_compatible",
    )

    assert EnvProviderAvailability().is_available("ollama") is False
    assert registry.require_known_provider("ollama").api_key_env == ""
