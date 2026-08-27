"""主 Agent 系统提示词的编译 (ADR-0018 §2.2, §4).

纯组装: 相同 PromptBuildInput 必须产出字节级相同的 text 与 fingerprint. 不读 os.environ,
不碰文件系统, 不调 LLM, 不读凭证 —— 需要什么由调用方注入.

**本文件只管编排, 不含任何正文** (ADR-0031, ADR-0039). 它回答三个问题: 出哪些块, 按什么
顺序, 哪些进稳定前缀; 以及每个块的开关与槽位值是什么. 正文与块内骨架在
`application/prompt/templates/`.

**这里不硬编码任何工具名, 也不硬编码任何模式的能力描述.** 工具用途取自
`ToolSpec.title`, 自动放行的能力取自 `fence_allowed_capabilities`.
两者都是既有的单一真相.
写死一份的后果是它会和真相各自演化, 而漂了不会报错: 提示词照常渲染, 只是内容开始骗人.

改正文要升 PROMPT_TEXT_VERSION 并更新指纹快照, 规矩写在 text.py 的模块说明里.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from forgecli.application.planning.planning_service import ActivePlanning
from forgecli.application.prompt.project_instruction_reader import ProjectInstruction
from forgecli.application.prompt.runtime_facts import RuntimeFacts
from forgecli.application.prompt.template_renderer import (
    PROMPT_TEXT_VERSION,
    render_block,
    render_heading,
    render_notice,
)
from forgecli.domain.execution.fence import FencePolicy
from forgecli.domain.intents import SessionMode
from forgecli.domain.memory.entry import MemoryEntry, MemoryScope
from forgecli.domain.prompt.blocks import PromptBlock, PromptBlockId, PromptSnapshot
from forgecli.domain.security.budget import fence_allowed_capabilities

__all__ = ["PromptBuildInput", "SystemPromptBuilder", "ToolBrief"]

_SHELL_TOOL = "shell_run"
# 任务拆解那一节点了 todo_write 与 plan_write 的名, 记忆那一节点了 memory_write 的名.
# 引导语跟着目录走: 它们不在本轮目录里就不该提名字, 否则等于告诉模型有个它看不见的工具,
# 而模型会去请求.
_PLANNING_TOOLS = frozenset({"todo_write", "plan_write"})
_MEMORY_TOOL = "memory_write"

# 项目指令的分隔行 (ADR-0018 5.3). 它们是 ASCII 结构标记, 不是措辞, 而且必须与
# `_escape_sentinels` 认的那一行逐字节相同 —— 所以留在转义器旁边, 不进模板.
_INSTRUCTION_BEGIN = "--- BEGIN WORKSPACE INSTRUCTIONS ---"
_INSTRUCTION_END = "--- END WORKSPACE INSTRUCTIONS ---"


@dataclass(frozen=True)
class ToolBrief:
    """一个工具在提示词里的一行: 名字 + 它自己的 title.

    title 取自 ToolSpec, 不在提示词层另写一份用途描述.
    """

    name: str
    title: str


@dataclass(frozen=True)
class PromptBuildInput:
    """编译一份主 Agent 提示词所需的全部输入.

    运行环境事实收在 RuntimeFacts 里而不是摊平成一堆字段: 那样"哪些环境事实可以渲染"
    这条规则就只需要在一个地方成立 (ADR-0018 §12).
    """

    mode: SessionMode
    facts: RuntimeFacts
    available_tools: tuple[ToolBrief, ...] = ()
    project_instructions: tuple[ProjectInstruction, ...] = field(default_factory=tuple)
    # 当前活动的计划与待办 (ADR-0022 §5.4). 空的是常态, 不是错误.
    planning: ActivePlanning = field(default_factory=ActivePlanning)
    # 本回合的围栏边界 (ADR-0030). 模型据此知道自己能碰到什么, 不必靠猜.
    fence: FencePolicy | None = None
    # 跨会话记忆 (ADR-0033 决策 8). 空的是常态 —— 新项目本来就没有记忆.
    memory: tuple[MemoryEntry, ...] = ()


@dataclass(frozen=True)
class _MemoryGroup:
    """记忆按分级分组后的一组. scope 用取值而不是枚举: 模板按它查标签."""

    scope: str
    entries: tuple[MemoryEntry, ...]


class SystemPromptBuilder:
    """把类型化输入编译成不可变的 PromptSnapshot."""

    def build(self, build_input: PromptBuildInput) -> PromptSnapshot:
        blocks: list[PromptBlock] = [
            _core_identity(),
            _tool_contract(build_input),
            _work_contract(build_input),
            _answer_contract(),
        ]
        # 条件性块: 没有项目指令就整块不渲染, 不留一个写着 "(无)" 的空标题.
        instructions = _workspace_instructions(build_input)
        if instructions is not None:
            blocks.append(instructions)
        blocks.append(_runtime_facts(build_input))
        # 计划与待办排在运行事实之后, 与它同属易变尾部. 顺序固定, 不因某块缺席而改变.
        plan_state = _plan_state(build_input)
        if plan_state is not None:
            blocks.append(plan_state)
        todo_state = _todo_state(build_input)
        if todo_state is not None:
            blocks.append(todo_state)
        # 记忆排在最后, 同属易变尾部: 模型可能在会话中途用 memory_write 改写它.
        memory_state = _memory_state(build_input)
        if memory_state is not None:
            blocks.append(memory_state)
        return PromptSnapshot(version=PROMPT_TEXT_VERSION, blocks=tuple(blocks))


# ---- 稳定前缀 ----


def _core_identity() -> PromptBlock:
    return PromptBlock(
        block_id=PromptBlockId.CORE_IDENTITY,
        heading=render_heading(PromptBlockId.CORE_IDENTITY),
        body=render_block(PromptBlockId.CORE_IDENTITY),
        cacheable=True,
    )


def _tool_contract(build_input: PromptBuildInput) -> PromptBlock:
    """工具契约, 工具表, shell 边界与检索顺序.

    各节跟着本轮目录走: 一个工具都没有的时候谈"先检索再读文件"是空话, 目录里没有
    shell_run 的时候提它的名字等于告诉模型有个它看不见的工具. 条件写在模板里, 这里只算
    开关.
    """
    names = {tool.name for tool in build_input.available_tools}
    return PromptBlock(
        block_id=PromptBlockId.TOOL_CONTRACT,
        heading=render_heading(PromptBlockId.TOOL_CONTRACT),
        body=render_block(
            PromptBlockId.TOOL_CONTRACT,
            tool_table=_tool_table(build_input.available_tools),
            has_shell=_SHELL_TOOL in names,
        ),
        cacheable=True,
    )


def _work_contract(build_input: PromptBuildInput) -> PromptBlock:
    """怎么把一件活干完: 交付纪律, 任务拆解, 写代码与记忆.

    与工具契约分块而不是并进去: 那一块管每一次工具调用, 这一块管整件事从接到手到交出去.
    合成一块之后, "什么时候该守哪条"就变模糊了 —— 与 answer_contract 单独成块同一个理由.

    整块进稳定前缀, 且不随模式变: 它只跟着目录走, 而目录在一档之内是稳定的.
    """
    names = {tool.name for tool in build_input.available_tools}
    return PromptBlock(
        block_id=PromptBlockId.WORK_CONTRACT,
        heading=render_heading(PromptBlockId.WORK_CONTRACT),
        body=render_block(
            PromptBlockId.WORK_CONTRACT,
            has_planning=names >= _PLANNING_TOOLS,
            has_memory=_MEMORY_TOOL in names,
        ),
        cacheable=True,
    )


def _answer_contract() -> PromptBlock:
    """怎么交付一个回答. 与工具契约分块而不是并进去: 两者的适用时机不同, 一个管每次
    工具调用, 一个只管最后那段文字, 混在一起会让"什么时候该守哪条"变模糊.
    """
    return PromptBlock(
        block_id=PromptBlockId.ANSWER_CONTRACT,
        heading=render_heading(PromptBlockId.ANSWER_CONTRACT),
        body=render_block(PromptBlockId.ANSWER_CONTRACT),
        cacheable=True,
    )


def _workspace_instructions(build_input: PromptBuildInput) -> PromptBlock | None:
    if not build_input.project_instructions:
        return None
    return PromptBlock(
        block_id=PromptBlockId.WORKSPACE_INSTRUCTIONS,
        heading=render_heading(PromptBlockId.WORKSPACE_INSTRUCTIONS),
        body=render_block(
            PromptBlockId.WORKSPACE_INSTRUCTIONS,
            # 包好再进模板: 分隔行与转义是安全机制 (ADR-0018 §5.3), 不是排版.
            instructions=tuple(
                _wrap_instruction(item) for item in build_input.project_instructions
            ),
        ),
        # FORGE.md 变更频率远低于每轮, 放进稳定前缀是划算的.
        cacheable=True,
    )


# ---- 易变尾部 ----


def _runtime_facts(build_input: PromptBuildInput) -> PromptBlock:
    facts = build_input.facts
    allowed = fence_allowed_capabilities(
        build_input.fence, confined=build_input.facts.isolation_level.contained
    )
    return PromptBlock(
        block_id=PromptBlockId.RUNTIME_FACTS,
        heading=render_heading(PromptBlockId.RUNTIME_FACTS),
        body=render_block(
            PromptBlockId.RUNTIME_FACTS,
            mode=build_input.mode.value,
            # 传取值而不是枚举: 模板按取值查名字表, 而顺序由那张表定, 与这里传的
            # 集合无关.
            auto_allowed=frozenset(capability.value for capability in allowed),
            platform=facts.platform,
            shell_kind=facts.shell_kind,
            isolation_level=facts.isolation_level.value,
            isolation_summary=facts.isolation_summary,
            working_directory=facts.working_directory,
            workspace_root=facts.workspace_roots[0],
            git_repository="yes" if facts.git_repository else "no",
            extra_roots=facts.workspace_roots[1:],
            tool_count=len(build_input.available_tools),
        ),
        # 每轮都可能变: 按一次 Tab 就换档. 它进稳定前缀就等于前缀不再稳定.
        cacheable=False,
    )


def _plan_state(build_input: PromptBuildInput) -> PromptBlock | None:
    """只放一行引用, 不放正文.

    计划正文可能很长而模型只在部分轮次需要它 —— ADR-0018 §4.4 的两条判据各命中一条,
    所以它走工具 (`plan_read`) 而不是每轮重述一遍.
    """
    plan = build_input.planning.live_plan
    if plan is None:
        # 没有计划, 或者它已经做完了. 后一种同样整块不渲染 —— 一份做完的计划每轮注入
        # 只会让模型去想它和当前这件事有没有关系.
        return None
    return PromptBlock(
        block_id=PromptBlockId.PLAN_STATE,
        heading=render_heading(PromptBlockId.PLAN_STATE),
        body=render_block(
            PromptBlockId.PLAN_STATE,
            plan_id=plan.plan_id,
            title=plan.title,
            status=plan.status.value,
            step_count=plan.step_count,
        ),
        cacheable=False,
    )


def _todo_state(build_input: PromptBuildInput) -> PromptBlock | None:
    """待办正文每轮都给.

    它小, 而且**每轮都要对齐** —— "当前该做哪一步"这件事只存在于对话历史里的话, 越往后
    越容易被稀释, 而那正是执行漂移的根因.
    """
    todo = build_input.planning.live_todo
    if todo is None or not todo.items:
        return None
    return PromptBlock(
        block_id=PromptBlockId.TODO_STATE,
        heading=render_heading(PromptBlockId.TODO_STATE),
        body=render_block(
            PromptBlockId.TODO_STATE,
            rendered=todo.render(),
            done=todo.done_count,
            total=todo.total_count,
        ),
        cacheable=False,
    )


def _memory_state(build_input: PromptBuildInput) -> PromptBlock | None:
    """跨会话记忆 (ADR-0033 决策 8).

    模板第一句就写明来源与优先级. 这一块与"项目指令"的**信任级别不同** —— 那边是用户
    写的, 这边是模型自己推断的, 可能已经过时 —— 而模型分不出来, 除非我们说.

    冲突由模型按这条优先级自己判, Forge 不替它挑一个: 逐条比对要求语义理解, 而机械
    地按 key 字面匹配几乎永不命中, 摆一个不会响的提示比没有更糟.
    """
    if not build_input.memory:
        return None
    groups = tuple(
        group
        for group in (_memory_group(scope, build_input.memory) for scope in MemoryScope)
        if group.entries
    )
    return PromptBlock(
        block_id=PromptBlockId.MEMORY_STATE,
        heading=render_heading(PromptBlockId.MEMORY_STATE),
        body=render_block(PromptBlockId.MEMORY_STATE, memory_groups=groups),
        cacheable=False,
    )


def _memory_group(scope: MemoryScope, memory: tuple[MemoryEntry, ...]) -> _MemoryGroup:
    """按分级分组. 顺序取自枚举声明顺序, 不另立一份分级清单.

    集合的迭代顺序不稳定会让同样的输入产出不同的提示词, 从而毁掉指纹的确定性; 枚举的
    声明顺序是稳定的.
    """
    return _MemoryGroup(
        scope=scope.value,
        entries=tuple(entry for entry in memory if entry.scope is scope),
    )


# ---- 渲染工具 ----


def _tool_table(tools: tuple[ToolBrief, ...]) -> str:
    """一行一个工具, 名字在前.

    名字在前是因为模型要用它发起调用; 而且左对齐的一列名字比左对齐的一列中文标题更好扫.
    与"当前运行事实"那一块的 `name: value` 同一种形状, 不另立一种.

    分隔符曾经在一次重构里丢过, 于是渲染出来的是 `读取文件fs_read` 这样粘在一起的
    一行. 它不会让任何测试失败 —— 提示词照常渲染, 指纹照常稳定, 只是模型读到的工具表
    是一坨. 这正是提示词类缺陷的典型形态: 没有任何一层会说话.
    """
    return "\n".join(f"  {tool.name}: {tool.title}" for tool in tools)


def _wrap_instruction(instruction: ProjectInstruction) -> str:
    """按信任标注包一份项目指令 (ADR-0018 §5.3)."""
    return "\n".join(
        (
            _INSTRUCTION_BEGIN,
            f"source: {instruction.source_id}",
            f"sha256: {instruction.digest}",
            "trust: below-forge-core",
            "",
            _escape_sentinels(instruction.text),
            _INSTRUCTION_END,
        )
    )


def _escape_sentinels(text: str) -> str:
    """转义正文里与分隔行同形的整行.

    不转义的话, 一份 FORGE.md 只要自己写一行 `--- END WORKSPACE INSTRUCTIONS ---`,
    后面的内容看起来就跑到了受信任区段里 —— 那是一条现成的提权路径.
    """
    return "\n".join(
        render_notice("prompt.instruction_escaped_line", line=line)
        if line.strip() in (_INSTRUCTION_BEGIN, _INSTRUCTION_END)
        else line
        for line in text.split("\n")
    )
