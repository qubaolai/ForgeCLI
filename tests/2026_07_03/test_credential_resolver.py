"""EnvCredentialResolver 契约（ADR-0011 §7，2026-07-03 切片；引用去前缀复核）。

覆盖：credential reference 即环境变量名（无 env: 前缀）、空引用拒绝、未配置凭证、
凭证值不泄露到 repr/str/异常。测试只使用假 key 名称与假值，不读真实环境。
"""

import pytest

from forgecli.application.llm.gateway.credentials import Credential
from forgecli.application.llm.gateway.errors import ModelAuthError
from forgecli.infrastructure.llm.credentials import EnvCredentialResolver


def _resolver(env: dict[str, str]) -> EnvCredentialResolver:
    return EnvCredentialResolver(getenv=env.get)


def test_resolves_env_var_name_to_credential() -> None:
    resolver = _resolver({"FAKE_KEY_ENV": "fake-value"})
    credential = resolver.resolve("deepseek", "FAKE_KEY_ENV")
    assert credential.provider_id == "deepseek"
    assert credential.ref == "FAKE_KEY_ENV"  # 引用即环境变量名，无前缀
    assert credential.value == "fake-value"


def test_rejects_blank_env_name() -> None:
    resolver = _resolver({})
    with pytest.raises(ModelAuthError):
        resolver.resolve("deepseek", "   ")


def test_missing_env_raises_auth_error_without_network() -> None:
    resolver = _resolver({})
    with pytest.raises(ModelAuthError) as excinfo:
        resolver.resolve("deepseek", "FAKE_KEY_ENV")
    assert "FAKE_KEY_ENV" in excinfo.value.message
    assert excinfo.value.provider == "deepseek"


def test_empty_env_value_treated_as_unconfigured() -> None:
    resolver = _resolver({"FAKE_KEY_ENV": "   "})
    with pytest.raises(ModelAuthError):
        resolver.resolve("deepseek", "FAKE_KEY_ENV")


def test_credential_value_never_leaks_in_repr_or_str() -> None:
    credential = Credential(
        provider_id="deepseek", ref="FAKE_KEY_ENV", value="super-secret"
    )
    assert "super-secret" not in repr(credential)
    assert "super-secret" not in str(credential)


def test_auth_error_message_never_contains_value() -> None:
    resolver = _resolver({"FAKE_KEY_ENV": ""})
    with pytest.raises(ModelAuthError) as excinfo:
        resolver.resolve("deepseek", "FAKE_KEY_ENV")
    assert "super-secret" not in str(excinfo.value)
