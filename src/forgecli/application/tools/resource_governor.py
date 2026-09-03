"""ResourceGovernor: 执行资源治理 (ADR-0014 §5).

超时, 输出上限, 产物上限和子进程回收属于**执行机制**, 不属于沙箱: 没有沙箱时它们照样
必须生效, 有沙箱时它们补上 Provider 报告不了的那部分. 所以它住在工具机制层.

一条注意: 输出截断必须是显式的. 悄悄截掉后半段, 模型会以为命令只输出了这些, 然后基于
残缺的输出做下一步决定.

上限的**口径**在 ADR-0041 决策 6 之后变了: 原先按"一次调用能占多少内存和磁盘"定 (那是
ADR-0014 的执行资源治理), 现在按"这条正文值不值得在往后每一轮都重发一遍"定. 数字因此
收紧了一半, 机制没变.
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.domain.tool.spec import ToolSpec

__all__ = ["ResourceGovernor", "ResourceLimits"]


@dataclass(frozen=True)
class ResourceLimits:
    timeout_seconds: float
    max_inline_bytes: int
    max_artifact_bytes: int

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须为正")


class ResourceGovernor:
    """按 ToolSpec 与全局上限算出本次调用的资源边界."""

    def __init__(
        self,
        *,
        max_timeout_seconds: float = 600.0,
        # 16 KiB ≈ 4k token. 与 ArtifactPolicy 的缺省同一个口径 (ADR-0041 决策 6):
        # 它是"单条结果最多能占窗口多少"的硬顶, 不是内存上限.
        max_inline_bytes: int = 16 * 1024,
        max_artifact_bytes: int = 32 * 1024 * 1024,
    ) -> None:
        self._max_timeout = max_timeout_seconds
        self._max_inline = max_inline_bytes
        self._max_artifact = max_artifact_bytes

    @property
    def max_inline_bytes(self) -> int:
        """单条结果最多能占窗口多少字节.

        暴露出来是给窗口水位用的 (ADR-0041 决策 8): 淘汰之后窗口里必然还留着最近那一条,
        连它自己都超过低水位的话, 淘汰多少次都没用. 那条判据要同时知道这个数和当前模型的
        窗口, 而只有装配层同时看得见两者.
        """
        return self._max_inline

    def limits_for(
        self, spec: ToolSpec, *, timeout_override: float | None = None
    ) -> ResourceLimits:
        """工具可以要求更短的超时, 但不能突破全局上限."""
        requested = timeout_override or spec.default_timeout_seconds
        return ResourceLimits(
            timeout_seconds=min(requested, self._max_timeout),
            max_inline_bytes=min(
                spec.artifact_policy.max_inline_bytes, self._max_inline
            ),
            max_artifact_bytes=min(
                spec.artifact_policy.max_artifact_bytes, self._max_artifact
            ),
        )

    @staticmethod
    def clamp(text: str, limit: int) -> tuple[str, bool]:
        """按**字节**截断并报告是否发生了截断 (多字节字符不会被切成半个)."""
        encoded = text.encode("utf-8")
        if len(encoded) <= limit:
            return text, False
        return encoded[:limit].decode("utf-8", errors="ignore"), True
