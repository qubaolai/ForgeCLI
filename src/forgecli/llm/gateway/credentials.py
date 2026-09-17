"""凭证的解析与轮换端口（ADR-0011 §6）。

Credential 值对象住在 domain.model.credentials；这里只留两个端口：按引用取凭证
（Resolver）与失败后轮换（Pool），具体实现在 infrastructure。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from pathlib import Path

from forgecli.common.errors import ConfigReadError
from forgecli.common.json_io import read_document, write_document
from forgecli.common.observability.log import get_log
from forgecli.core.model.credentials import Credential
from forgecli.llm.gateway.errors import (
    ModelAuthError,
    ModelQuotaExhaustedError,
    ModelRateLimitError,
)

_log = get_log(__name__)

# ---- 原 infrastructure/llm/credentials.py ----
# 凭证端口实现：环境变量解析 + 进程内凭证池（ADR-0011 §7）。
#
# EnvCredentialResolver 把 credential reference 直接当作环境变量名解析：
#     - 引用为空 / 全空白 → ModelAuthError。
#     - 环境变量未设置或为空 → ModelAuthError（**不发起任何网络请求**）。
#     - 错误消息只含环境变量名，绝不含凭证值（§7 / §19 安全不变量）。
#
# InMemoryCredentialPool 维护凭证冷却状态：
#     - get_credential 按 refs 顺序取第一个未冷却且可解析的凭证；直接返回
#       `Credential`，不做租借 / 占用，凭证可被并发调用同时取得。
#     - mark_failed(retry_after) 进入冷却（秒级, 只在进程内），冷却到期或
#       mark_succeeded 后恢复。
#     - mark_exhausted(cooldown) 是配额耗尽那一种（ADR-0059）: 小时级, 且经
#       CredentialCooldownStore 落盘 —— 下一个进程启动时不再拿这把 key 去撞一次.
# mark_failed 未给 retry_after 时的默认冷却秒数。
_DEFAULT_COOLDOWN_SECONDS = 30.0


class EnvCredentialResolver:
    """把 credential reference 当作环境变量名读取。

    环境读取通过注入的 getenv 完成，测试可用 fake 隔离（不 monkeypatch 全局）。
    """

    def __init__(self, *, getenv: Callable[[str], str | None] | None = None) -> None:
        if getenv is None:
            import os

            getenv = os.environ.get
        self._getenv = getenv

    def resolve(self, provider_id: str, credential_ref: str) -> Credential:
        """解析一个 credential reference；失败抛 ModelAuthError。"""
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


class CredentialCooldownStore:
    """配额耗尽的冷却截止时刻落盘 (ADR-0059 决策 5).

    文件里只有 `环境变量名 -> 墙钟截止 (epoch 秒)`. 环境变量名本来就写在 llm.json
    里, 这里没有多暴露任何东西; 明文 key 从不经过这个类.

    读坏了 (JSON 语法错, 权限问题) 当作空表并记一条警告, 不让一次模型调用因为一份
    状态文件失败: 最坏的结果只是多撞一次配额耗尽的 key, 而那正是没有这个文件时的
    行为.

    每次写都先重读再合并: 同一台机器上可能有几个 forge 进程, 各自只该改自己知道的
    那一条, 而不是用自己启动时的快照盖掉别人后来写的.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self, *, now: float) -> dict[str, float]:
        """未过期的 `ref -> 墙钟截止`. 过期的条目在这里丢掉, 下次写盘时自然清掉."""
        try:
            raw = read_document(self._path, label="凭证冷却状态文件")
        except ConfigReadError as exc:
            _log.warning(
                "credential_cooldown.unreadable", path=str(self._path), error=str(exc)
            )
            return {}
        deadlines: dict[str, float] = {}
        for ref, value in raw.items():
            if isinstance(value, int | float) and float(value) > now:
                deadlines[ref] = float(value)
        return deadlines

    def set(self, ref: str, deadline: float, *, now: float) -> None:
        merged = self.load(now=now)
        merged[ref] = deadline
        self._write(merged)

    def clear(self, ref: str, *, now: float) -> None:
        merged = self.load(now=now)
        if merged.pop(ref, None) is None:
            return
        self._write(merged)

    def _write(self, deadlines: Mapping[str, float]) -> None:
        try:
            write_document(self._path, deadlines)
        except OSError as exc:
            _log.warning(
                "credential_cooldown.unwritable", path=str(self._path), error=str(exc)
            )


class InMemoryCredentialPool:
    """进程内凭证池：按序选取 + 冷却表.

    秒级冷却 (限流 / 认证失败) 只在进程内; 配额耗尽那一种经 `store` 落盘, 见
    CredentialCooldownStore. 不传 store 就全部只在进程内.
    """

    def __init__(
        self,
        resolver: EnvCredentialResolver,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        default_cooldown: float = _DEFAULT_COOLDOWN_SECONDS,
        store: CredentialCooldownStore | None = None,
    ) -> None:
        self._resolver = resolver
        self._clock = clock
        self._wall_clock = wall_clock
        self._default_cooldown = default_cooldown
        self._store = store
        # credential_ref -> 冷却截止时刻（monotonic 秒）。
        self._cooldown_until: dict[str, float] = {}
        # 其中哪些是配额耗尽. 全部 key 都在冷却时要告诉调用方是哪一种: "等几秒"和
        # "去充值"是两句不同的话.
        self._exhausted: set[str] = set()
        if store is not None:
            # 落盘的是墙钟时刻, 表里记的是单调钟: 按"还剩多少秒"换算, 两种钟各管各的.
            now_wall = wall_clock()
            now = clock()
            for ref, deadline in store.load(now=now_wall).items():
                self._cooldown_until[ref] = now + (deadline - now_wall)
                self._exhausted.add(ref)

    def get_credential(
        self, provider_id: str, credential_refs: tuple[str, ...]
    ) -> Credential:
        """按 refs 顺序返回第一个可用（未冷却且可解析）的凭证。

        凭证只有可用 / 冷却两态：`mark_failed` / `mark_exhausted` 使其进入冷却，
        `mark_succeeded` 或冷却到期后恢复；没有其他不可用状态。凭证不被独占：
        并发调用可同时拿到同一凭证，本方法不做任何并发限制、不需要配对的归还
        操作。

        无可用凭证时抛的错误按原因分 (ADR-0059 决策 4): 未配置 / 全部解析失败是
        ModelAuthError (去检查环境变量); 全部在冷却期是 ModelRateLimitError, 带最短
        的剩余秒数 (等一会儿); 冷却的全是配额耗尽则是 ModelQuotaExhaustedError (去充值
        或换账号). 原先三种都报认证失败, 而"请检查 API Key 环境变量"对后两种是误导.
        """
        if not credential_refs:
            raise ModelAuthError(
                f"provider {provider_id!r} 未配置任何 credential reference",
                provider=provider_id,
            )
        now = self._clock()
        last_error: ModelAuthError | None = None
        remaining: list[float] = []
        for ref in credential_refs:
            until = self._cooldown_until.get(ref, 0.0)
            if until > now:
                remaining.append(until - now)
                continue  # 冷却器内不选
            try:
                return self._resolver.resolve(provider_id, ref)
            except ModelAuthError as exc:
                last_error = exc
                continue
        if len(remaining) == len(credential_refs):
            retry_after = min(remaining)
            if all(ref in self._exhausted for ref in credential_refs):
                raise ModelQuotaExhaustedError(
                    f"provider {provider_id!r} 的全部凭证配额均已耗尽",
                    provider=provider_id,
                    retry_after=retry_after,
                )
            raise ModelRateLimitError(
                f"provider {provider_id!r} 的全部凭证均在冷却期内",
                provider=provider_id,
                retry_after=retry_after,
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
        """标记凭证失败并进入冷却；冷却期内不再被 get_credential 选中。"""
        cooldown = retry_after if retry_after is not None else self._default_cooldown
        self._cooldown_until[credential_ref] = self._clock() + cooldown
        self._exhausted.discard(credential_ref)

    def mark_exhausted(self, credential_ref: str, cooldown: float) -> None:
        """配额耗尽: 冷却 `cooldown` 秒, 并落盘让下一个进程也知道 (ADR-0059)."""
        self._cooldown_until[credential_ref] = self._clock() + cooldown
        self._exhausted.add(credential_ref)
        if self._store is not None:
            now_wall = self._wall_clock()
            self._store.set(credential_ref, now_wall + cooldown, now=now_wall)

    def mark_succeeded(self, credential_ref: str) -> None:
        """标记凭证成功，清除其冷却状态。"""
        self._cooldown_until.pop(credential_ref, None)
        self._exhausted.discard(credential_ref)
        if self._store is not None:
            self._store.clear(credential_ref, now=self._wall_clock())
