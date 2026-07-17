"""凭证解析与凭证池端口（ADR-0011 §7）。

凭证集中由 `CredentialResolver` 解析、由 `CredentialPool` 按调用选取：
    - 配置文件只保存 credential reference（环境变量名，如 ``DEEPSEEK_API_KEY``），
      不保存明文 key。
    - provider adapter 不枚举、不切换 key；它只接收本次调用的 `Credential`。
    - `get_credential` 直接返回凭证，不做租借 / 占用：凭证不被独占，多个并发
      调用可同时拿到同一凭证的值，网关不对凭证做并发限制（ForgeCLI 是个人
      单用户 CLI，不存在需要互斥访问同一凭证的场景）。
    - 凭证可用性只在两种情况下变化：`mark_failed` 使其进入冷却（对应
      401/429），或引用本身在配置/解析层面消失（credential_refs 被修改、
      resolver 解析失败，如环境变量被删）——后者不需要池维护任何状态，
      每次 `get_credential` 都现查现解析。
    - 凭证不写入事件或日志。

安全约束（§7 / §19）：`Credential.value` 不进入 repr / str / 异常 / 日志；
测试只能使用假 key 名称或 fake resolver。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Credential:
    """一次调用可用的凭证。value 为 secret，绝不出现在 repr / str / 日志。"""

    provider_id: str
    ref: str
    value: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("Credential.provider_id 不能为空")
        if not self.ref.strip():
            raise ValueError("Credential.ref 不能为空")

    def __str__(self) -> str:
        # 只暴露 provider 与 ref（引用名本身不含 secret），value 一律掩码。
        return f"Credential({self.provider_id}, {self.ref}, ***)"


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
