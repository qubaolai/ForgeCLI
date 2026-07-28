"""凭证的解析与轮换端口（ADR-0011 §6）。

Credential 值对象住在 domain.model.credentials；这里只留两个端口：按引用取凭证
（Resolver）与失败后轮换（Pool），具体实现在 infrastructure。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from forgecli.domain.model.credentials import Credential


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
