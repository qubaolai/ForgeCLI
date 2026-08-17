"""脚本静态分析的产物 (ADR-0013 §8).

一句话概括这个类型的语义边界: **它发现风险, 不证明安全.**

`confidence` 与 `opaque_constructs` 因此是一等字段. 反射, 动态加载, 依赖安装脚本和
运行期生成的代码都超出静态可见范围; "没发现危险信号"只能表示没发现, 在无沙箱的 auto
下不能凭它跳过 Background Safety Classifier.

内容哈希绑定分析结果: 执行前内容变了就必须重新分析, 否则就是"分析的是一份脚本, 执行的
是另一份"的 TOCTOU.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from forgecli.domain.tool.hashing import digest, digest_text

__all__ = ["ScriptFacts", "ScriptSignal", "ScriptSnapshot"]


@dataclass(frozen=True)
class ScriptSignal:
    """一条确定性信号: 在脚本里的什么位置发现了什么."""

    code: str
    detail: str
    line: int | None = None


@dataclass(frozen=True)
class ScriptFacts:
    """ADR-0013 §8 要求提取的能力事实."""

    content_hash: str
    language: str
    files_read: tuple[str, ...] = ()
    files_written: tuple[str, ...] = ()
    network_access: bool = False
    child_process: bool = False
    dynamic_execution: bool = False
    privilege_or_system_change: bool = False
    credential_access: bool = False
    external_side_effect: bool = False
    resource_exhaustion_signal: bool = False
    opaque_constructs: tuple[str, ...] = ()
    evidence: tuple[ScriptSignal, ...] = ()
    # 0.0 表示完全没能读懂; 1.0 表示内容完整且全部构造都能静态判定.
    confidence: float = 0.0
    dependency_hash: str | None = None
    # 内容没能完整收集时为 True (入口脚本读到了, 但它 import 的东西展不开).
    incomplete: bool = field(default=False)

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence 必须在 [0, 1] 之间")

    @property
    def hard_signals(self) -> tuple[ScriptSignal, ...]:
        """可以不经 LLM 直接 DENY 的硬性信号."""
        return tuple(
            signal for signal in self.evidence if signal.code in _HARD_SIGNAL_CODES
        )

    @property
    def needs_classifier(self) -> bool:
        """静态分析不足以下结论时为 True.

        注意它**不是** "有没有发现危险" 的反面: 即使一条危险都没发现, 只要内容不完整或
        存在 opaque 构造, 仍然需要分类器. 这正是 §8 说的"未发现危险只能表示未发现".
        """
        return (
            self.incomplete
            or bool(self.opaque_constructs)
            or self.dynamic_execution
            or self.confidence < _CONFIDENT_ENOUGH
        )

    @property
    def cache_key(self) -> str:
        """内容 + 依赖 + 语言. 策略与执行环境由缓存层再叠一层 (§12)."""
        return digest(
            {
                "content_hash": self.content_hash,
                "dependency_hash": self.dependency_hash,
                "language": self.language,
            }
        )


_CONFIDENT_ENOUGH = 0.8

_HARD_SIGNAL_CODES = frozenset(
    {
        "privilege_escalation",
        "protected_path_write",
        "credential_exfiltration",
        "download_and_execute",
        "reverse_shell",
        "destructive_wipe",
        "startup_config_write",
        "git_hook_write",
    }
)


@dataclass(frozen=True)
class ScriptSnapshot:
    """一份被分析过的脚本正文快照.

    它是"分析读过的那一份"的凭据, 三个下游都要它: 审批界面逐字展示, 学习规则绑内容
    哈希, 审计记录被拦下的是哪段代码. 三者都不能自己再读一次文件 —— 那就是又开一个
    TOCTOU 窗口.

    origin 区分 inline / heredoc / file / encoded: "命令里写着的一行"与"磁盘上那个
    文件"对用户是完全不同的两件事, 界面必须说清.
    """

    language: str
    origin: str
    source: str
    path: str | None = None

    @property
    def content_hash(self) -> str:
        return digest_text(self.source)
