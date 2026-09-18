"""上下文组装: 把一次调用的材料拼成一份 AssembledContext (ADR-0056 决策 2 / 3 / 7 / 12).

对外唯一入口. 三件事:

- system prompt: 静态段每次现算 (纯函数, 便宜), 会话段按名缓存, 只在刷新点重算.
- 通知: 交给 NoticeAssembler 按窗口推增量, 这里只把结果放进 AssembledContext.
- 可观测: 三个位置各一个 sha256; 两次组装之间没有刷新点而指纹变了, warn 并把 diff 写到
  会话目录 —— 用来抓"谁又把每轮变的东西放进了前缀".

不碰窗口: 那是 `WindowManager` 的事. 一个负责"这次调用的上下文长什么样", 一个负责"窗口
该不该淘汰", 合起来会让"为什么这一轮变贵了"再也说不清是哪一半干的.
"""

from __future__ import annotations

import difflib
import os
from collections.abc import Callable
from dataclasses import dataclass

from forgecli.common.observability.log import get_log
from forgecli.conversation.notices import (
    NoticeAssembler,
    NoticeInputs,
    memory_digest,
)
from forgecli.core.agent.state import AssembledContext, ContextFingerprints
from forgecli.core.context.budget import ContextBudget
from forgecli.core.context.window import Window
from forgecli.core.permissions import PermissionMode
from forgecli.core.token_estimator import ApproximateTokenEstimator
from forgecli.core.tool.file_state import FileStateCache
from forgecli.core.tool.hashing import digest, digest_text
from forgecli.core.tool.tool_call import ToolSchema
from forgecli.memory.entry import MemoryEntry
from forgecli.planning.service import ActivePlanning
from forgecli.prompt import prompts
from forgecli.prompt.project_instruction_reader import (
    FsProjectInstructionReader,
    ProjectInstruction,
)
from forgecli.prompt.sections import SectionCache
from forgecli.prompt.session_facts import SessionFacts

__all__ = ["AssemblyInputs", "ContextAssembler"]

_log = get_log(__name__)


@dataclass(frozen=True)
class AssemblyInputs:
    """一次组装的材料. 全部由 AgentTurnService 现读, 这里不碰服务."""

    window: Window
    mode: PermissionMode
    facts: SessionFacts
    today: str
    instructions: tuple[ProjectInstruction, ...] = ()
    planning: ActivePlanning | None = None
    memory: tuple[MemoryEntry, ...] = ()
    tools: tuple[ToolSchema, ...] = ()
    budget: ContextBudget | None = None
    read_files: FileStateCache | None = None


class ContextAssembler:
    """会话级. 一个 AgentTurnService 一份."""

    def __init__(
        self,
        *,
        instructions: FsProjectInstructionReader,
        diagnostics_dir: Callable[[], str] | None = None,
        estimator: ApproximateTokenEstimator | None = None,
    ) -> None:
        self._sections = SectionCache()
        self._notices = NoticeAssembler(instructions=instructions)
        self._diagnostics_dir = diagnostics_dir
        self._estimator = estimator or ApproximateTokenEstimator()
        # 会话段里那份记忆快照的摘要. 通知拿它判断"记忆是不是被别的进程改了".
        self._memory_baseline = ""
        self._start_date = ""
        self._refresh_pending = "session_start"
        self._evicted_seen = 0
        self._previous: ContextFingerprints | None = None
        self._previous_text: tuple[str, str] = ("", "")

    def refresh(self, reason: str) -> None:
        """会话刷新点 (决策 7): 下一次组装重算会话段, 通知的基线跟着重置."""
        self._refresh_pending = reason

    def mark_seen(self, kind: str, revision: str) -> None:
        """模型在一批工具结果里看过了这个版本 (决策 5), 不再为它发通知."""
        self._notices.mark_seen(kind, revision)

    def assemble(self, inputs: AssemblyInputs) -> AssembledContext:
        after_compaction = inputs.window.evicted_count > self._evicted_seen
        if after_compaction:
            self._evicted_seen = inputs.window.evicted_count
            self.refresh("compaction")
        refreshed = self._refresh_pending
        if refreshed:
            self._sections.clear()
            self._memory_baseline = memory_digest(inputs.memory)
            self._start_date = inputs.facts.start_date
            self._refresh_pending = ""

        tool_names = frozenset(schema.name for schema in inputs.tools)
        sections = prompts.get_system_prompt(
            tool_names=tool_names,
            facts=inputs.facts,
            instructions=inputs.instructions,
            memory=inputs.memory,
            cache=self._sections,
        )
        if refreshed:
            self._notices.reset_baselines(tool_names)
        static_text, session_text = prompts.split_system_prompt(sections)
        fingerprints = ContextFingerprints(
            static=digest_text(static_text),
            session=digest_text(session_text),
            tools=digest(inputs.tools),
        )
        self._detect_cache_break(fingerprints, static_text, session_text, refreshed)

        notices = self._notices.build(
            NoticeInputs(
                window=inputs.window,
                mode=inputs.mode,
                today=inputs.today,
                start_date=self._start_date,
                memory_baseline=self._memory_baseline,
                tool_names=tool_names,
                planning=inputs.planning or ActivePlanning(),
                memory=inputs.memory,
                read_files=inputs.read_files,
                workspace_roots=inputs.facts.workspace_roots,
                after_compaction=after_compaction,
                file_token_room=(
                    self._file_token_room(inputs, static_text, session_text)
                    if after_compaction
                    else None
                ),
            )
        )
        context = AssembledContext(
            system_prompt=prompts.system_prompt_text(sections),
            fingerprints=fingerprints,
            tools=inputs.tools,
            budget=inputs.budget,
            notices=notices,
        )
        _log.info(
            "context.assembled",
            static_sha=fingerprints.static[:12],
            session_sha=fingerprints.session[:12],
            tools_sha=fingerprints.tools[:12],
            refreshed=refreshed or None,
            static_tokens=self._estimator.estimate_text(static_text),
            session_tokens=self._estimator.estimate_text(session_text),
            session_sections=self._sections.sizes(),
            tools=[schema.name for schema in inputs.tools],
            notices=[item.notice_kind for item in notices],
        )
        # 整段只在 debug 下写: 它每次几千字, info 级别会把日志淹掉, 而"模型到底看到了
        # 什么"恰恰是最需要能翻出来的一件事.
        _log.debug("context.system_prompt", text=context.system_prompt)
        return context

    def _file_token_room(
        self, inputs: AssemblyInputs, static_text: str, session_text: str
    ) -> int | None:
        """压缩后重注入文件还能占多少 token: 低水位减去当前窗口 (决策 9).

        重注入发生在这次的水位检查之后, 没有这个上限它会把刚压下去的窗口再撑过高水位,
        下一次调用又压一次; 小窗口模型上就是两次溢出后停轮.
        """
        if inputs.budget is None:
            return None
        prefix = self._estimator.estimate_input(
            messages=(), system_prompt=static_text + session_text, tools=inputs.tools
        )
        used = self._estimator.estimate_input(messages=inputs.window.messages)
        return max(0, inputs.budget.window_floor(prefix) - used)

    def _detect_cache_break(
        self,
        current: ContextFingerprints,
        static_text: str,
        session_text: str,
        refreshed: str,
    ) -> None:
        previous = self._previous
        self._previous = current
        previous_text = self._previous_text
        self._previous_text = (static_text, session_text)
        if previous is None or refreshed:
            return
        changed = [
            name
            for name in ("static", "session")
            if getattr(previous, name) != getattr(current, name)
        ]
        if not changed:
            return
        diff_path = self._write_diff(previous_text, (static_text, session_text))
        _log.warning(
            "context.cache_break",
            changed=changed,
            diff=diff_path,
        )

    def _write_diff(
        self, before: tuple[str, str], after: tuple[str, str]
    ) -> str | None:
        if self._diagnostics_dir is None:
            return None
        lines = list(
            difflib.unified_diff(
                "\n\n".join(before).splitlines(),
                "\n\n".join(after).splitlines(),
                fromfile="previous",
                tofile="current",
                lineterm="",
            )
        )
        try:
            directory = self._diagnostics_dir()
            os.makedirs(directory, exist_ok=True)
            stamp = self._previous.static[:8] if self._previous else "x"
            path = os.path.join(directory, f"cache-break-{stamp}.diff")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(lines))
            return path
        except OSError:
            return None
