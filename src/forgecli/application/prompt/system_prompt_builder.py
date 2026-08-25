"""主 Agent 系统提示词的编译 (ADR-0018 §2.2, §4).

纯组装: 相同 PromptBuildInput 必须产出字节级相同的 text 与 fingerprint. 不读 os.environ,
不碰文件系统, 不调 LLM, 不读凭证 —— 需要什么由调用方注入.

**本文件只管编排, 不含任何正文** (ADR-0031). 每一段文字都在
`domain/prompt/text.py`, 与循环里的引导, 收摊通知放在一处 —— 散着看不出彼此矛盾, 收在
一屏之内才查得出"回答契约要求半角而我们自己用全角"这类问题.

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
from forgecli.domain.execution.fence import FencePolicy
from forgecli.domain.intents import SessionMode
from forgecli.domain.memory.entry import MemoryEntry
from forgecli.domain.prompt import text as prompt_text
from forgecli.domain.prompt.blocks import PromptBlock, PromptBlockId, PromptSnapshot
from forgecli.domain.security.budget import fence_allowed_capabilities
from forgecli.domain.tool.capability import Capability

__all__ = ["PromptBuildInput", "SystemPromptBuilder", "ToolBrief"]

_SHELL_TOOL = "shell_run"


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


class SystemPromptBuilder:
    """把类型化输入编译成不可变的 PromptSnapshot."""

    def build(self, build_input: PromptBuildInput) -> PromptSnapshot:
        blocks: list[PromptBlock] = [
            _core_identity(),
            _tool_contract(build_input),
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
        return PromptSnapshot(
            version=prompt_text.PROMPT_TEXT_VERSION, blocks=tuple(blocks)
        )


# ---- 稳定前缀 ----


def _core_identity() -> PromptBlock:
    return PromptBlock(
        block_id=PromptBlockId.CORE_IDENTITY,
        heading=prompt_text.HEADING_IDENTITY,
        body=prompt_text.CORE_IDENTITY,
        cacheable=True,
    )


def _tool_contract(build_input: PromptBuildInput) -> PromptBlock:
    names = {tool.name for tool in build_input.available_tools}
    has_shell = _SHELL_TOOL in names
    sections = [prompt_text.TOOL_CONTRACT]
    table = _tool_table(build_input.available_tools)
    if table:
        # 引导语也跟着目录走: shell_run 不在本轮目录里就不该提它的名字, 否则等于告诉
        # 模型有个它看不见的工具, 而模型会去请求.
        lead = (
            prompt_text.TOOL_TABLE_LEAD_WITH_SHELL
            if has_shell
            else prompt_text.TOOL_TABLE_LEAD_PLAIN
        )
        sections.append(f"{prompt_text.SECTION_TOOL_CHOICE}\n\n{lead}\n\n{table}")
    if has_shell:
        sections.append(prompt_text.SHELL_BOUNDARY)
    if table:
        # 检索顺序跟着目录走: 一个工具都没有的时候谈"先检索再读文件"是空话.
        sections.append(
            f"{prompt_text.SECTION_SEARCH_ORDER}\n\n{prompt_text.SEARCH_STRATEGY}"
        )
    return PromptBlock(
        block_id=PromptBlockId.TOOL_CONTRACT,
        heading=prompt_text.HEADING_TOOL_CONTRACT,
        body="\n\n".join(sections),
        cacheable=True,
    )


def _answer_contract() -> PromptBlock:
    """怎么交付一个回答. 与工具契约分块而不是并进去: 两者的适用时机不同, 一个管每次
    工具调用, 一个只管最后那段文字, 混在一起会让"什么时候该守哪条"变模糊.
    """
    return PromptBlock(
        block_id=PromptBlockId.ANSWER_CONTRACT,
        heading=prompt_text.HEADING_ANSWER_CONTRACT,
        body=prompt_text.ANSWER_CONTRACT,
        cacheable=True,
    )


def _workspace_instructions(build_input: PromptBuildInput) -> PromptBlock | None:
    if not build_input.project_instructions:
        return None
    parts = [prompt_text.WORKSPACE_INSTRUCTIONS_LEAD]
    parts.extend(_wrap_instruction(item) for item in build_input.project_instructions)
    return PromptBlock(
        block_id=PromptBlockId.WORKSPACE_INSTRUCTIONS,
        heading=prompt_text.HEADING_WORKSPACE_INSTRUCTIONS,
        body="\n\n".join(parts),
        # FORGE.md 变更频率远低于每轮, 放进稳定前缀是划算的.
        cacheable=True,
    )


# ---- 易变尾部 ----


def _runtime_facts(build_input: PromptBuildInput) -> PromptBlock:
    facts = build_input.facts
    allowed = fence_allowed_capabilities(
        build_input.fence, confined=build_input.facts.isolation_level.contained
    )
    rows: list[tuple[str, str]] = [
        ("mode", build_input.mode.value),
        (prompt_text.FACTS_LABEL_AUTO_ALLOWED, _capability_names(allowed)),
        (prompt_text.FACTS_LABEL_NEEDS_HUMAN, prompt_text.FACTS_NEEDS_HUMAN_VALUE),
        ("platform", facts.platform),
        ("shell", facts.shell_kind),
        ("isolation", f"{facts.isolation_level.value}   {facts.isolation_summary}"),
        ("path", prompt_text.FACTS_PATH_NOTE),
        ("working_directory", facts.working_directory),
        ("workspace_roots", facts.workspace_roots[0]),
        ("git_repository", "yes" if facts.git_repository else "no"),
    ]
    lines = [f"{name}: {value}" for name, value in rows]
    lines.extend(
        prompt_text.FACTS_EXTRA_ROOT.format(root=extra)
        for extra in facts.workspace_roots[1:]
    )
    lines.append(f"tools: {len(build_input.available_tools)} 个")
    return PromptBlock(
        block_id=PromptBlockId.RUNTIME_FACTS,
        heading=prompt_text.HEADING_RUNTIME_FACTS,
        body="\n".join(lines),
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
        heading=prompt_text.HEADING_PLAN_STATE,
        body=prompt_text.PLAN_STATE_BODY.format(
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
        heading=prompt_text.HEADING_TODO_STATE,
        body=prompt_text.TODO_STATE_BODY.format(
            rendered=todo.render(),
            done=todo.done_count,
            total=todo.total_count,
        ),
        cacheable=False,
    )


def _capability_names(allowed: frozenset[Capability]) -> str:
    """把自动放行的能力集渲染成中文.

    从 fence_allowed_capabilities 现取, 不在提示词层再维护一份模式描述 —— 围栏策略
    变了这里会跟着变, 而散文不会.
    """
    return ", ".join(
        name
        for capability, name in prompt_text.CAPABILITY_NAMES
        if capability in allowed
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
            prompt_text.INSTRUCTION_BEGIN,
            f"source: {instruction.source_id}",
            f"sha256: {instruction.digest}",
            "trust: below-forge-core",
            "",
            _escape_sentinels(instruction.text),
            prompt_text.INSTRUCTION_END,
        )
    )


def _escape_sentinels(text: str) -> str:
    """转义正文里与分隔行同形的整行.

    不转义的话, 一份 FORGE.md 只要自己写一行 `--- END WORKSPACE INSTRUCTIONS ---`,
    后面的内容看起来就跑到了受信任区段里 —— 那是一条现成的提权路径.
    """
    return "\n".join(
        f"{prompt_text.INSTRUCTION_ESCAPED_PREFIX}{line}"
        if line.strip() in (prompt_text.INSTRUCTION_BEGIN, prompt_text.INSTRUCTION_END)
        else line
        for line in text.split("\n")
    )


def _memory_state(build_input: PromptBuildInput) -> PromptBlock | None:
    """跨会话记忆 (ADR-0033 决策 8).

    正文第一句就写明来源与优先级. 这一块与"项目指令"的**信任级别不同** —— 那边是用户
    写的, 这边是模型自己推断的, 可能已经过时 —— 而模型分不出来, 除非我们说.

    冲突由模型按这条优先级自己判, Forge 不替它挑一个: 逐条比对要求语义理解, 而机械
    地按 key 字面匹配几乎永不命中, 摆一个不会响的提示比没有更糟.
    """
    if not build_input.memory:
        return None
    sections: list[str] = [prompt_text.MEMORY_STATE_LEAD]
    for scope, label in prompt_text.MEMORY_SCOPE_NAMES:
        lines = [
            prompt_text.MEMORY_ENTRY_LINE.format(key=entry.key, value=entry.value)
            for entry in build_input.memory
            if entry.scope is scope
        ]
        if lines:
            sections.append("\n".join([f"{label}:", *lines]))
    return PromptBlock(
        block_id=PromptBlockId.MEMORY_STATE,
        heading=prompt_text.HEADING_MEMORY_STATE,
        body="\n\n".join(sections),
        cacheable=False,
    )
