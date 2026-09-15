"""凭证的解析与轮换端口（ADR-0011 §6）。

Credential 值对象住在 domain.model.credentials；这里只留两个端口：按引用取凭证
（Resolver）与失败后轮换（Pool），具体实现在 infrastructure。
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable

from forgecli.core.model.credentials import Credential
from forgecli.llm.gateway.errors import ModelAuthError


class CredentialResolver(ABC):
    """把 credential reference 解析成凭证值的端口。

    credential reference 就是**环境变量名**（无 scheme 前缀）；唯一实现
    `EnvCredentialResolver` 直接读该环境变量（infrastructure/llm/credentials.py）。
    dotenv / keychain 等来源与 ``env:`` 前缀经复核判定个人单用户 CLI 不需要，
    已移除（2026-07-17，见 ADR-0011 §7）。解析失败抛 ModelAuthError，错误消息
    只含环境变量名，不含凭证值。
    """

    @abstractmethod
    def resolve(self, provider_id: str, credential_ref: str) -> Credential:
        """解析一个 credential reference；失败抛 ModelAuthError。"""


class CredentialPool(ABC):
    """同一 provider 多凭证的选取 / 冷却端口（§7）。

    auth 失败、429 或配额问题时，gateway 依次尝试该 provider 的其他
    credential_refs（受 max_retries 约束），任何重试均不切换 provider/model。
    """

    @abstractmethod
    def get_credential(
        self, provider_id: str, credential_refs: tuple[str, ...]
    ) -> Credential:
        """按 refs 顺序返回第一个可用（未冷却且可解析）的凭证。

        凭证只有可用 / 冷却两态：`mark_failed` 使其进入冷却（401/429），
        `mark_succeeded` 或冷却到期后恢复；没有其他不可用状态。凭证不被独占：
        并发调用可同时拿到同一凭证，本方法不做任何并发限制、不需要配对的归还
        操作。无可用凭证（未配置 / 全部冷却 / 全部解析失败）
        抛 ModelAuthError。
        """

    @abstractmethod
    def mark_failed(
        self,
        credential_ref: str,
        error_type: str,
        retry_after: float | None = None,
    ) -> None:
        """标记凭证失败并进入冷却；冷却期内不再被 get_credential 选中。"""

    @abstractmethod
    def mark_succeeded(self, credential_ref: str) -> None:
        """标记凭证成功，清除其冷却状态。"""


# ---- 原 infrastructure/llm/credentials.py ----
# 凭证端口实现：环境变量解析 + 进程内凭证池（ADR-0011 §7）。
#
# EnvCredentialResolver 把 credential reference 直接当作环境变量名解析：
#     - 引用为空 / 全空白 → ModelAuthError。
#     - 环境变量未设置或为空 → ModelAuthError（**不发起任何网络请求**）。
#     - 错误消息只含环境变量名，绝不含凭证值（§7 / §19 安全不变量）。
#
# InMemoryCredentialPool 维护凭证冷却状态（仅进程内，不落盘）：
#     - get_credential 按 refs 顺序取第一个未冷却且可解析的凭证；直接返回
#       `Credential`，不做租借 / 占用，凭证可被并发调用同时取得。
#     - mark_failed(retry_after) 进入冷却，冷却到期或 mark_succeeded 后恢复。
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
