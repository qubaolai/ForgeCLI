"""主 Agent 系统提示词的编译 (ADR-0042 决策 1).

纯组装: 相同输入必须产出字节级相同的 text 与 fingerprint. 不读 os.environ, 不碰文件系统,
不调 LLM, 不读凭证 —— 需要什么由调用方注入.

**本文件只管编排, 不含任何正文** (ADR-0031, ADR-0039). 它回答两个问题: 出哪些块, 按什么
顺序. 正文与块内骨架在 `application/prompt/templates/blocks/`.

## 它现在只收一个参数

ADR-0041 把运行上下文与状态迁去 `application/context` 之后, 提示词只依赖两样东西: 包里
的模板, 和 `FORGE.md`. 于是原先的 `PromptBuildInput` 只剩一个字段 —— 而一个单字段包装
正是 `ModePolicy` 被删时的形状 (ADR-0028 规则 C), 所以它一起删了, `build` 直接收元组.

没有 mode, 没有 ToolCatalog, 没有计划待办与记忆: 传不进来, 也就不可能在这一层泄进提示词.

## 提示词里一个工具名都不出现

tool schema 已经带了 name, description 与 parameters, 那就是模型选工具的全部依据.
单个工具怎么用住在它自己的 `ToolSpec.description`, 只在该工具在本轮目录里时才付费,
而且模型是在考虑调用它的时候读到 (ADR-0042 决策 5). 有用例查渲染结果里不出现任何
`ToolSpec.name`.

改正文要升 PROMPT_TEXT_VERSION 并更新指纹快照, 规矩写在 template_renderer 的模块说明里.
"""

from __future__ import annotations

from functools import lru_cache

from forgecli.application.llm.gateway.token_estimator import ApproximateTokenEstimator
from forgecli.application.prompt.project_instruction_reader import ProjectInstruction
from forgecli.application.prompt.sentinels import (
    WORKSPACE_BEGIN,
    WORKSPACE_END,
    escape_sentinels,
)
from forgecli.application.prompt.template_renderer import (
    PROMPT_STATIC_BUDGET,
    render_block,
    render_heading,
)
from forgecli.domain.prompt.blocks import PromptBlock, PromptBlockId, PromptSnapshot
from forgecli.domain.prompt.errors import PromptBudgetExceeded

__all__ = ["SystemPromptBuilder"]

# 无状态, 无 IO, 只做字符折算. 用模块级实例而不是每次新建或注入: 它没有第二种实现,
# 也没有任何配置, 造一个注入点只是多一层解包.
_ESTIMATOR = ApproximateTokenEstimator()


class SystemPromptBuilder:
    """把项目指令编译成不可变的 PromptSnapshot."""

    def build(
        self, instructions: tuple[ProjectInstruction, ...] = ()
    ) -> PromptSnapshot:
        blocks = list(_static_blocks())
        # 条件性块: 没有项目指令就整块不渲染, 不留一个写着 "(无)" 的空标题.
        workspace = _workspace_instructions(instructions)
        if workspace is not None:
            blocks.append(workspace)
        return PromptSnapshot(blocks=tuple(blocks))


@lru_cache(maxsize=1)
def _static_blocks() -> tuple[PromptBlock, ...]:
    """五个内置块. 只依赖包版本, 所以一个进程编译一次 (ADR-0042 决策 1).

    预算校验放在这里而不是 `build` 里: 它查的是**静态**部分, 而 `FORGE.md` 是用户写的,
    大小由 ProjectInstructionReader 自己的预算管. 放这里还顺带让校验只跑一次.
    """
    blocks = (
        _block(PromptBlockId.CORE_IDENTITY),
        _block(PromptBlockId.SAFETY_RULES),
        _block(PromptBlockId.TOOL_PROTOCOL),
        _block(PromptBlockId.WORK_RULES),
        _block(PromptBlockId.ANSWER_RULES),
    )
    estimated = _ESTIMATOR.estimate_text(
        "\n\n".join(block.render() for block in blocks)
    )
    if estimated > PROMPT_STATIC_BUDGET:
        raise PromptBudgetExceeded(estimated=estimated, budget=PROMPT_STATIC_BUDGET)
    return blocks


def _block(block_id: PromptBlockId) -> PromptBlock:
    """五个内置块的形状完全一样: 标题查表, 正文渲染模板, 没有槽位.

    槽位在 ADR-0042 之前是有的 (工具表, 模式能力, 计划待办), 那些输入现在都不存在了 ——
    于是五个各写一遍的函数塌成这一个.
    """
    return PromptBlock(
        block_id=block_id,
        heading=render_heading(block_id),
        body=render_block(block_id),
    )


def _workspace_instructions(
    instructions: tuple[ProjectInstruction, ...],
) -> PromptBlock | None:
    if not instructions:
        return None
    return PromptBlock(
        block_id=PromptBlockId.WORKSPACE_INSTRUCTIONS,
        heading=render_heading(PromptBlockId.WORKSPACE_INSTRUCTIONS),
        body=render_block(
            PromptBlockId.WORKSPACE_INSTRUCTIONS,
            # 包好再进模板: 分隔行与转义是安全机制 (ADR-0018 §5.3), 不是排版.
            instructions=tuple(_wrap_instruction(item) for item in instructions),
        ),
    )


def _wrap_instruction(instruction: ProjectInstruction) -> str:
    """按信任标注包一份项目指令 (ADR-0018 §5.3)."""
    return "\n".join(
        (
            WORKSPACE_BEGIN,
            f"source: {instruction.source_id}",
            f"sha256: {instruction.digest}",
            "trust: below-forge-core",
            "",
            escape_sentinels(instruction.text, WORKSPACE_BEGIN, WORKSPACE_END),
            WORKSPACE_END,
        )
    )
