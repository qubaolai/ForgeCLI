"""系统提示词的纯值对象 (ADR-0018 §3.1).

提示词是**块序列**而不是一段文本. 扁平字符串在块数上去之后堵死两件事: 按块的缓存划分,
以及为无 system role 的 Provider 重排. 把 text 降级成派生属性的成本几乎为零 —— 指纹仍然
算在渲染后的完整文本上.

缓存断点由 cacheable 表达: cacheable=True 的块构成稳定前缀, 第一个 False 之后全是每轮
重算的易变尾部. 构造时校验这个划分不被打破 —— 一个混在尾部里的可缓存块不会报错, 只会
让供应商的自动前缀缓存从命中整段退化成命中开头几十字符, 而这件事没有任何一层会说话.

这里不引入 Path, importlib.resources 或任何 Provider 类型: 编译顺序属 application,
文件读取属 infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from forgecli.domain.tool.hashing import digest_text

__all__ = ["PromptBlock", "PromptBlockId", "PromptSnapshot"]


class PromptBlockId(Enum):
    """块标识. 值进诊断输出, 不可随意改.

    枚举里**没有** todo_state 与 plan_state: 那两块随 ADR-0022 落地时再加 (ADR-0018
    §14 阶段 5). 提前建空块会让"这块为什么永远不渲染"变成一个要去翻提交历史的问题.
    """

    CORE_IDENTITY = "core_identity"
    TOOL_CONTRACT = "tool_contract"
    WORKSPACE_INSTRUCTIONS = "workspace_instructions"
    RUNTIME_FACTS = "runtime_facts"


@dataclass(frozen=True)
class PromptBlock:
    """提示词的一个小节."""

    block_id: PromptBlockId
    heading: str
    body: str
    cacheable: bool = True

    def __post_init__(self) -> None:
        if not self.heading.strip():
            raise ValueError(f"{self.block_id.value} 的 heading 不能为空")
        if not self.body.strip():
            # 条件性块该在 builder 里就不生成, 而不是生成一个空块.
            raise ValueError(f"{self.block_id.value} 的 body 不能为空")

    def render(self) -> str:
        return f"# {self.heading}\n\n{self.body.strip()}"


@dataclass(frozen=True)
class PromptSnapshot:
    """一个 turn 内冻结的提示词 (ADR-0018 §6.2).

    text 与 fingerprint 是**派生**属性: 调用方无法传入与块序列不匹配的值.

    version 不是装饰: 它是排查"模型行为什么时候变的"唯一的锚点, 且 ADR-0018 §15.3 要求
    改内置文本必须同时升版本并更新快照测试.
    """

    version: int
    blocks: tuple[PromptBlock, ...]
    # 派生字段. compare=False 让它们不参与相等比较, 也不进 canonical 哈希.
    text: str = field(default="", compare=False)
    fingerprint: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("PromptSnapshot.version 必须为正整数")
        if not self.blocks:
            raise ValueError("PromptSnapshot.blocks 不能为空")
        self._assert_cache_partition()
        text = "\n\n".join(block.render() for block in self.blocks)
        object.__setattr__(self, "text", text)
        # 指纹不放回文本: 自引用会让指纹不稳定 (ADR-0018 §8.1).
        object.__setattr__(self, "fingerprint", digest_text(f"{self.version}\n{text}"))

    def _assert_cache_partition(self) -> None:
        """可缓存块必须全部排在不可缓存块之前."""
        seen_volatile = False
        for block in self.blocks:
            if not block.cacheable:
                seen_volatile = True
                continue
            if seen_volatile:
                raise ValueError(
                    f"{block.block_id.value} 声明 cacheable, "
                    "却排在易变块之后; 稳定前缀因此不成立"
                )
