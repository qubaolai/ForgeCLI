"""凭证端口实现：环境变量解析 + 进程内凭证池（ADR-0011 §7）。

EnvCredentialResolver 把 credential reference 直接当作环境变量名解析：
    - 引用为空 / 全空白 → ModelAuthError。
    - 环境变量未设置或为空 → ModelAuthError（**不发起任何网络请求**）。
    - 错误消息只含环境变量名，绝不含凭证值（§7 / §19 安全不变量）。

InMemoryCredentialPool 维护凭证冷却状态（仅进程内，不落盘）：
    - get_credential 按 refs 顺序取第一个未冷却且可解析的凭证；直接返回
      `Credential`，不做租借 / 占用，凭证可被并发调用同时取得。
    - mark_failed(retry_after) 进入冷却，冷却到期或 mark_succeeded 后恢复。
"""

from __future__ import annotations

import time
from collections.abc import Callable

from forgecli.application.llm.gateway.credentials import (
    Credential,
    CredentialPool,
    CredentialResolver,
)
from forgecli.application.llm.gateway.errors import ModelAuthError

# mark_failed 未给 retry_after 时的默认冷却秒数。
_DEFAULT_COOLDOWN_SECONDS = 30.0


class EnvCredentialResolver(CredentialResolver):
    """把 credential reference 当作环境变量名读取。

    环境读取通过注入的 getenv 完成，测试可用 fake 隔离（不 monkeypatch 全局）。
    """

    def __init__(self, *, getenv: Callable[[str], str | None] | None = None) -> None:
        if getenv is None:
            import os

            getenv = os.environ.get
        self._getenv = getenv

    def resolve(self, provider_id: str, credential_ref: str) -> Credential:
        env_name = credential_ref.strip()
        if not env_name:
            raise ModelAuthError(
                "凭证引用非法：环境变量名不能为空", provider=provider_id
            )
        value = self._getenv(env_name)
        if not value or not value.strip():
            raise ModelAuthError(
                f"未配置凭证：环境变量 {env_name} 未设置或为空",
                provider=provider_id,
            )
        return Credential(provider_id=provider_id, ref=env_name, value=value)


class InMemoryCredentialPool(CredentialPool):
    """进程内凭证池：按序选取 + 冷却表。状态不落盘、不跨 session。"""

    def __init__(
        self,
        resolver: CredentialResolver,
        *,
        clock: Callable[[], float] = time.monotonic,
        default_cooldown: float = _DEFAULT_COOLDOWN_SECONDS,
    ) -> None:
        self._resolver = resolver
        self._clock = clock
        self._default_cooldown = default_cooldown
        # credential_ref -> 冷却截止时刻（monotonic 秒）。
        self._cooldown_until: dict[str, float] = {}

    def get_credential(
        self, provider_id: str, credential_refs: tuple[str, ...]
    ) -> Credential:
        if not credential_refs:
            raise ModelAuthError(
                f"provider {provider_id!r} 未配置任何 credential reference",
                provider=provider_id,
            )
        now = self._clock()
        last_error: ModelAuthError | None = None
        all_cooling = True
        for ref in credential_refs:
            if self._cooldown_until.get(ref, 0.0) > now:
                continue  # 冷却器内不选
            all_cooling = False
            try:
                return self._resolver.resolve(provider_id, ref)
            except ModelAuthError as exc:
                last_error = exc
                continue
        if all_cooling:
            raise ModelAuthError(
                f"provider {provider_id!r} 的全部凭证均在冷却期内",
                provider=provider_id,
            )
        if last_error is not None:
            raise last_error
        raise ModelAuthError(
            f"provider {provider_id!r} 无可用凭证", provider=provider_id
        )

    def mark_failed(
        self,
        credential_ref: str,
        error_type: str,
        retry_after: float | None = None,
    ) -> None:
        cooldown = retry_after if retry_after is not None else self._default_cooldown
        self._cooldown_until[credential_ref] = self._clock() + cooldown

    def mark_succeeded(self, credential_ref: str) -> None:
        self._cooldown_until.pop(credential_ref, None)
