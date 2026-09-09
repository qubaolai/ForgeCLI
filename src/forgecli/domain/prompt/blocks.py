"""系统提示词的纯值对象 (ADR-0042 §3.1).

提示词是**块序列**而不是一段文本. 块的作用不是缓存划分 —— 那条理由已经不成立 ——
而是让条件性块能整块缺席: 没有 FORGE.md 时 `WORKSPACE_INSTRUCTIONS` 根本不渲染,
而不是渲染一个写着"(无)"的空标题. 扁平字符串做不到这件事, 只能在拼接里写 if.

## 为什么删掉了 cacheable

原先每个块带一个 `cacheable` 标记, 并有一条校验保证可缓存块全排在易变块之前.
它买不到任何东西: 请求是 `[tools][system][messages]`, 整个 system prompt 都排在
messages 之前, 块内部怎么分区都换不来一个字节的缓存. 接 Anthropic 那种显式断点也不行 ——
断点只划得出 system 内部的前缀, 易变尾部仍在 messages 之前, 历史永远进不了缓存.

ADR-0042 把全部会话内可变的内容迁出提示词之后, 这里剩下的块**全都是静态的**, 那条
分区校验因此永远平凡成立. 删它是自然结果, 不是妥协 (ADR-0028 规则 C).

这里不引入 Path, importlib.resources 或任何 Provider 类型: 编译顺序属 application,
文件读取属 infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from forgecli.domain.tool.hashing import digest_text

__all__ = ["PromptBlock", "PromptBlockId", "PromptSnapshot"]


class PromptBlockId(Enum):
    """块标识. 值即模板文件名, 也进诊断输出, 不可随意改."""

    CORE_IDENTITY = "core_identity"
    # 不可让渡的边界. 与工具协议分块是因为**改动门槛不同**: 删一条工作方式是取舍,
    # 删一条安全边界要走安全评审. 混在一块里, 两者的手续就一样了.
    SAFETY_RULES = "safety_rules"
    # 调用时序与并发. 只管每一次工具往返, 不管单个工具怎么用 —— 后者住在
    # ToolSpec.description, 提示词里一个工具名都不出现 (ADR-0042 决策 5).
    TOOL_PROTOCOL = "tool_protocol"
    # 怎么把活干完 (交付纪律, 任务拆解, 写代码, 记忆). 与工具协议分块是因为适用时机
    # 不同: 那一块管每一次工具调用, 这一块管整件事从接到手到交出去.
    WORK_RULES = "work_rules"
    ANSWER_RULES = "answer_rules"
    # FORGE.md. 唯一随会话变的块 —— 其余五块只依赖包版本.
    WORKSPACE_INSTRUCTIONS = "workspace_instructions"


@dataclass(frozen=True)
class PromptBlock:
    """提示词的一个小节."""

    block_id: PromptBlockId
    heading: str
    body: str

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
    """一份编译好的提示词.

    text 与 fingerprint 是**派生**属性: 调用方无法传入与块序列不匹配的值.

    version 不是装饰: 它是排查"模型行为什么时候变的"唯一的锚点, 且 ADR-0042 要求改
    内置正文必须同时升版本并更新快照测试.
    """

    blocks: tuple[PromptBlock, ...]
    # 派生字段. compare=False 让它们不参与相等比较, 也不进 canonical 哈希.
    text: str = field(default="", compare=False)
    fingerprint: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        if not self.blocks:
            raise ValueError("PromptSnapshot.blocks 不能为空")
        text = "\n\n".join(block.render() for block in self.blocks)
        object.__setattr__(self, "text", text)
        # 指纹不放回文本: 自引用会让指纹不稳定.
        object.__setattr__(self, "fingerprint", digest_text(f"{text}"))
