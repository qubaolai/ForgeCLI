"""凭证获取 + 有界重试环 (ADR-0011 §7 / §12, ADR-0012 §2).

**绝不切换 provider/model.** 重试换的只可能是凭证, 或者原地等一会儿再来 —— 换模型是
用户的选择, 网关替他换等于把一次失败悄悄变成"在另一个模型上成功了".

分出来是因为它原先有**两份一模一样的抄写**: 非流式一份, 流式首包前一份。两份的
except 分支, 计数器与冷却逻辑完全相同, 差别只在"这一次尝试做什么"。改一份漏另一份
不会有任何东西报错 —— 它只会让两条路在同样的故障下表现不同, 而这种偏差要撞上真实的
供应商故障才看得见。现在两条路走同一个 ``run``, 差异由传进来的 ``attempt`` 表达。

错误分类表 (§12, ADR-0059):

=========================  ===================================================
ModelCancelledError        直接上抛, 不重试 —— 用户已经不要这个结果了
ModelQuotaExhaustedError   这把 key 没额度了: 长冷却 (小时级, 落盘), 换下一把
ModelRateLimitError        带 retry_after 且不超阈值: 原地等, **同一凭证**重试
                           否则: 冷却这把凭证, 换下一把
ModelAuthError             冷却这把凭证, 换下一把; keyless 时无凭证可换, 上抛
Timeout/Unavailable        同凭证有限重试, 记一次 transport 失败
其余 ModelGatewayError     上抛, 不重试
其余任何异常               归一化成 ModelProviderInternalError 上抛
=========================  ===================================================

两条互不相干的界 (ADR-0059 决策 3):

- **换凭证**以凭证池为界: 每把 key 在一次调用里最多被冷却一次, 全部冷却过就把最后
  那个 provider 错误上抛. 配了几把 key 就能试几把, 不看 ``max_retries``.
- **同凭证重试** (429 短等, 超时 / 连接失败) 以 ``max_retries`` 为界, 两类共用一份
  预算. 换 key 对这两类没有帮助 —— 超时是服务的事, 不是 key 的事.

原先两类都计入 ``max_retries`` (默认 2), 于是配了五把 key 也只轮到前三把, 而那一条
限制在配置界面上看不出来.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from forgecli.common.observability.log import get_log
from forgecli.core.model.credentials import Credential
from forgecli.core.model.model_ref import ModelRef
from forgecli.core.model.request import ModelRequest
from forgecli.llm.gateway.credentials import InMemoryCredentialPool
from forgecli.llm.gateway.errors import (
    ModelAuthError,
    ModelCancelledError,
    ModelGatewayError,
    ModelProviderInternalError,
    ModelQuotaExhaustedError,
    ModelRateLimitError,
    ModelTimeoutError,
    ModelUnavailableError,
    raise_if_cancelled,
    with_context,
)
from forgecli.llm.gateway.governance import SlidingWindowHealthRegistry
from forgecli.llm.gateway.provider_settings import ProviderRuntimeSettings

__all__ = ["CredentialRetryLoop", "RetryCounts", "credential_fields"]

_log = get_log(__name__)

_T = TypeVar("_T")


def credential_fields(credential: Credential | None) -> dict[str, object]:
    """凭证在日志里的身份: 引用名 + 值的哈希前缀, 不含值本身.

    排查"用的是哪一把 key"要的是身份而不是那串字符; 而日志文件是最容易被整份贴进
    issue 的东西. 指纹取 sha256 前 8 位而不是"头 4 位 + 尾 4 位": 后者泄漏真实字符.
    """
    if credential is None:
        return {"credential": "keyless"}
    digest = hashlib.sha256(credential.value.encode("utf-8")).hexdigest()[:8]
    return {"credential": credential.ref, "credential_fingerprint": digest}


@dataclass
class RetryCounts:
    """一次调用内的重试计数（ADR-0012 §2）；进入 raw_metadata 与观测样本。"""

    credential: int = 0
    transport: int = 0
    wait: int = 0

    def summary(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if self.credential:
            out["credential_retries"] = str(self.credential)
        if self.transport:
            out["transport_retries"] = str(self.transport)
        if self.wait:
            out["wait_retries"] = str(self.wait)
        return out


class CredentialRetryLoop:
    """按上面那张表重试一次 provider 调用。

    ``attempt`` 收一个凭证 (keyless 时为 None), 返回这次调用的结果。它自己组 provider
    请求 —— 组请求要知道模型条目, 参数合并与 thinking 档, 那些是网关的知识。
    """

    def __init__(
        self,
        credential_pool: InMemoryCredentialPool | None,
        health: SlidingWindowHealthRegistry,
        *,
        sleeper: Callable[[float], None],
    ) -> None:
        self._credentials = credential_pool
        self._health = health
        self._sleeper = sleeper

    def run(
        self,
        attempt: Callable[[Credential | None], _T],
        *,
        request: ModelRequest,
        ref: ModelRef,
        settings: ProviderRuntimeSettings,
        counts: RetryCounts,
        what: str = "调用",
        mark_success: bool = True,
    ) -> _T:
        """``mark_success=False`` 表示"这次尝试返回了, 但还不算成功"。

        流式就是这种情况: ``attempt`` 拿到首包就返回, 而首包之后仍然可能中断 —— 凭证
        要等整条流走完才算好用, 由调用方在收尾处自己调 ``mark_succeeded``。
        """
        last_error: ModelGatewayError | None = None
        # 同凭证重试的预算: 短等与传输失败共用. 换凭证不花它.
        retries_left = settings.max_retries
        # 本次调用里已经冷却过的 key. 凭证池按冷却截止时刻选 key, 而截止时刻可能在
        # 我们试别的 key 的这几秒里过去 (Retry-After: 0 甚至立刻过去) —— 于是同一把
        # key 会被再选一次, 环就没有界了. 这张表让"每把 key 至多一次"不依赖时钟.
        cooled: set[str] = set()
        while True:
            raise_if_cancelled(request, ref)
            try:
                credential = self.credential_for(request, ref, settings)
            except ModelGatewayError:
                if last_error is not None:
                    # 凭证池耗尽（全部冷却 / 失败）：上抛最后一次 provider 错误。
                    raise last_error from None
                raise
            if credential is not None and credential.ref in cooled:
                assert last_error is not None
                raise last_error from None

            try:
                result = attempt(credential)
            except ModelCancelledError:
                raise
            except ModelQuotaExhaustedError as exc:
                last_error = with_context(exc, ref, request)
                _log.warning(
                    "llm.quota_exhausted",
                    provider=ref.provider,
                    model=ref.model,
                    cooldown_seconds=settings.quota_cooldown_seconds,
                    message=str(exc),
                    **credential_fields(credential),
                )
                if credential is None:
                    raise last_error from exc  # keyless：无凭证可换
                self._exhaust(credential, settings.quota_cooldown_seconds)
                cooled.add(credential.ref)
                counts.credential += 1
                continue
            except ModelRateLimitError as exc:
                last_error = with_context(exc, ref, request)
                _log.warning(
                    "llm.rate_limited",
                    provider=ref.provider,
                    model=ref.model,
                    retry_after=exc.retry_after,
                    message=str(exc),
                    **credential_fields(credential),
                )
                if _should_wait(exc, settings) and retries_left > 0:
                    # 429 短等重试（ADR-0012 §2）：本地等待后同一凭证重试；
                    # 等待经注入 sleeper，计入 latency，不计 provider 计费。
                    retries_left -= 1
                    counts.wait += 1
                    assert exc.retry_after is not None
                    self._sleeper(exc.retry_after)
                    continue
                if credential is None:
                    raise last_error from exc  # keyless：无凭证可换
                self._cool_down(credential, exc, exc.retry_after)
                cooled.add(credential.ref)
                counts.credential += 1
                continue
            except ModelAuthError as exc:
                # 凭证级重试：同 provider/model 换下一个 credential（§12）。
                last_error = with_context(exc, ref, request)
                _log.warning(
                    "llm.auth_failed",
                    provider=ref.provider,
                    model=ref.model,
                    message=str(exc),
                    **credential_fields(credential),
                )
                if credential is None:
                    raise last_error from exc
                self._cool_down(credential, exc, None)
                cooled.add(credential.ref)
                counts.credential += 1
                continue
            except (ModelTimeoutError, ModelUnavailableError) as exc:
                # 连接 / 超时：同凭证有限重试（§12 处理表）。
                self._health.record_failure(ref)
                last_error = with_context(exc, ref, request)
                if retries_left == 0:
                    raise last_error from exc
                retries_left -= 1
                counts.transport += 1
                _log.warning(
                    "llm.transport_retry",
                    provider=ref.provider,
                    model=ref.model,
                    attempt=counts.transport,
                    max_retries=settings.max_retries,
                    error=type(exc).__name__,
                    message=str(exc),
                )
                continue
            except ModelGatewayError:
                raise
            except Exception as exc:  # 兜底：任何非网关异常归一化为网关错误。
                self._health.record_failure(ref)
                _log.exception(
                    "llm.provider_internal_error",
                    provider=ref.provider,
                    model=ref.model,
                    error=type(exc).__name__,
                    message=str(exc),
                )
                raise ModelProviderInternalError(
                    f"provider {ref.provider!r} {what}失败: {exc}",
                    provider=ref.provider,
                    model=ref.model,
                    request_id=request.request_id,
                ) from exc
            else:
                if mark_success:
                    self.mark_succeeded(credential)
                return result

    # ---- 凭证 ----

    def credential_for(
        self,
        request: ModelRequest,
        ref: ModelRef,
        settings: ProviderRuntimeSettings,
    ) -> Credential | None:
        """获取本次调用凭证；keyless（无 refs）或未接凭证池时为 None。

        未配置凭证时在此抛 ModelAuthError, 全部冷却时抛 ModelRateLimitError /
        ModelQuotaExhaustedError (见 InMemoryCredentialPool.get_credential) ——
        **不发起任何网络请求**（§7 / §19）。
        凭证不被独占：并发调用可拿到同一凭证，网关不做凭证维度并发限制。
        """
        if self._credentials is None or not settings.credential_refs:
            return None
        try:
            return self._credentials.get_credential(
                ref.provider, settings.credential_refs
            )
        except ModelGatewayError as exc:
            raise with_context(exc, ref, request) from exc

    def mark_succeeded(self, credential: Credential | None) -> None:
        if credential is not None and self._credentials is not None:
            self._credentials.mark_succeeded(credential.ref)

    def _cool_down(
        self, credential: Credential, exc: ModelGatewayError, retry_after: float | None
    ) -> None:
        if self._credentials is not None:
            self._credentials.mark_failed(
                credential.ref, type(exc).__name__, retry_after
            )

    def _exhaust(self, credential: Credential, cooldown: float) -> None:
        if self._credentials is not None:
            self._credentials.mark_exhausted(credential.ref, cooldown)


def _should_wait(exc: ModelRateLimitError, settings: ProviderRuntimeSettings) -> bool:
    """429 短等判定（ADR-0012 §2）：retry_after 存在且不超过配置阈值。"""
    return (
        exc.retry_after is not None
        and exc.retry_after <= settings.wait_threshold_seconds
    )
