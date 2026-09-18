"""窗口通知的组装: 增量, 节流, 预算 (ADR-0056 决策 4 / 5 / 6 / 7 / 9).

Claude Code 的 attachments 在本仓库的对应物. 每次调模型前算一次"这次要新发哪几条", 判据
全部从**窗口本身**推出来: 同类型的上一条通知在哪, 它之后有几个用户回合 / 几次模型调用 /
调过哪些工具. 不另存计数器 —— 压缩淘汰掉旧通知, 计数自然归零, 重注入也就自然发生.

原则: **模型已经在窗口里看过的内容不再发.** 计划与待办的绝大多数变化由模型自己用工具
造成, tool_result 里已经有新状态; 只有外部改动 (revision 变了, 而窗口里没有对应的工具
调用), 提醒, 与压缩 / 恢复后的重注入三种情形才发.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from forgecli.common.observability.log import get_log
from forgecli.core.context.window import Window
from forgecli.core.message import ChatMessage, TextBlock
from forgecli.core.permissions import PermissionMode
from forgecli.core.token_estimator import ApproximateTokenEstimator
from forgecli.core.tool.file_state import FileStateCache, normalize_path
from forgecli.core.tool.hashing import digest_text
from forgecli.core.tool.names import (
    MEMORY_FORGET_TOOL_NAME,
    MEMORY_WRITE_TOOL_NAME,
    PLAN_READ_TOOL_NAME,
    PLAN_WRITE_TOOL_NAME,
    TODO_SET_STATUS_TOOL_NAME,
    TODO_WRITE_TOOL_NAME,
)
from forgecli.core.turn import MessageRole
from forgecli.memory.entry import MemoryEntry
from forgecli.planning.plan import PlanDocument
from forgecli.planning.service import ActivePlanning
from forgecli.planning.todo import TodoList
from forgecli.prompt import prompts
from forgecli.prompt.project_instruction_reader import (
    INSTRUCTION_FILE_NAME,
    FsProjectInstructionReader,
)

__all__ = [
    "MEMORY_TOOLS",
    "PLAN_TOOLS",
    "TODO_TOOLS",
    "NoticeAssembler",
    "NoticeInputs",
    "guidance",
    "memory_digest",
    "notice",
    "plan_revision",
    "todo_revision",
]

_log = get_log(__name__)

# 通知类型名. 进消息元数据与事件日志, 不可随意改.
KIND_MODE = "mode"
KIND_MODE_EXIT = "mode_exit"
KIND_PLAN = "plan"
KIND_TODO = "todo"
KIND_TODO_REMINDER = "todo_reminder"
KIND_MEMORY = "memory"
KIND_TOOLS = "tools"
KIND_DATE = "date"
KIND_NESTED_INSTRUCTIONS = "nested_instructions"
KIND_RECENT_FILE = "recent_file"
KIND_WORKSPACE_CHANGED = "workspace_changed"
# 循环规则回填的引导: 空转提醒, 格式重试, 收工具. 同一个包裹, 同一个 meta 标记.
KIND_LOOP_GUIDANCE = "loop_guidance"

# 离开这两档要说一声: 它们改变了模型能做什么, 不说它会继续按旧档位行事.
_ANNOUNCED_EXIT = (PermissionMode.PLAN, PermissionMode.AUTO)
# 调过这些工具的那一批之后, 模型就看过了对应状态的当前版本 (决策 5): 由
# AgentTurnService 在每批工具结果之后 mark_seen, 通知只在"版本与模型看过的不同"时发.
PLAN_TOOLS = frozenset({PLAN_WRITE_TOOL_NAME, PLAN_READ_TOOL_NAME})
TODO_TOOLS = frozenset({TODO_WRITE_TOOL_NAME, TODO_SET_STATUS_TOOL_NAME})
MEMORY_TOOLS = frozenset({MEMORY_WRITE_TOOL_NAME, MEMORY_FORGET_TOOL_NAME})
_CHARS_PER_TOKEN = 3


def notice(kind: str, revision: str, text: str) -> ChatMessage:
    """一条通知的形状只有这一份."""
    return ChatMessage(
        role=MessageRole.USER,
        content=(TextBlock(text),),
        meta=True,
        notice_kind=kind,
        notice_revision=revision,
    )


def guidance(text: str) -> ChatMessage:
    """循环规则要追加进窗口的一句引导. 包上标记, 标成 meta."""
    return notice(KIND_LOOP_GUIDANCE, "", prompts.wrap_reminder(text))


def memory_digest(entries: Iterable[MemoryEntry]) -> str:
    return digest_text("\n".join(f"{e.scope.value}:{e.key}={e.value}" for e in entries))


def plan_revision(plan: PlanDocument) -> str:
    """计划的版本键. 状态也算: 评审改了状态, 模型要知道."""
    return f"{plan.plan_id}:{plan.revision}:{plan.status.value}"


def todo_revision(todo: TodoList) -> str:
    return f"{todo.todo_id}:{todo.revision}"


@dataclass(frozen=True)
class NoticeInputs:
    window: Window
    mode: PermissionMode
    today: str
    # 会话段里写的那个日期: 与它相同就不用说.
    start_date: str
    # 会话段里那份记忆快照的摘要: 与它相同就不是外部改动.
    memory_baseline: str
    tool_names: frozenset[str]
    planning: ActivePlanning = field(default_factory=ActivePlanning)
    memory: tuple[MemoryEntry, ...] = ()
    read_files: FileStateCache | None = None
    workspace_roots: tuple[str, ...] = ()
    # 上一次组装以来窗口被淘汰过: 重注入 (决策 9).
    after_compaction: bool = False
    # 重注入文件能占多少 token: 由组装器按当前预算算, 不让重注入把刚压下去的窗口再撑
    # 过水位. None 表示没有预算, 用 POST_COMPACT_TOKEN_BUDGET.
    file_token_room: int | None = None


class NoticeAssembler:
    """会话级. 只存两样窗口里推不出来的东西: 工具池的基线, 记忆通知的累计字节."""

    def __init__(
        self,
        *,
        instructions: FsProjectInstructionReader,
        estimator: ApproximateTokenEstimator | None = None,
    ) -> None:
        self._instructions = instructions
        self._estimator = estimator or ApproximateTokenEstimator()
        self._announced_tools: frozenset[str] | None = None
        self._memory_notice_bytes = 0
        # 模型在自己的工具结果里看过的版本 (kind -> revision). 压缩把那些结果淘汰掉了,
        # 所以刷新点一并清空, 下一次按"没看过"重注入.
        self._seen: dict[str, str] = {}

    def reset_baselines(self, tool_names: frozenset[str]) -> None:
        """会话刷新点: 会话段刚重算, 它里面的工具清单就是新的基线."""
        self._announced_tools = tool_names
        self._memory_notice_bytes = 0
        self._seen.clear()

    def mark_seen(self, kind: str, revision: str) -> None:
        """一批工具结果之后, 模型看过了这个版本."""
        self._seen[kind] = revision

    def build(self, inputs: NoticeInputs) -> tuple[ChatMessage, ...]:
        window = inputs.window
        notices: list[ChatMessage] = []
        notices.extend(self._mode(window, inputs.mode))
        notices.extend(self._plan(window, inputs))
        notices.extend(self._todo(window, inputs))
        notices.extend(self._memory(window, inputs))
        notices.extend(self._tools(inputs.tool_names))
        notices.extend(self._date(window, inputs.today, inputs.start_date))
        notices.extend(self._nested_instructions(window, inputs))
        batch = _cap_batch(notices)
        # 记忆的会话预算只记真正发出去的: 被整批上限丢掉的那条不算.
        self._memory_notice_bytes += sum(
            _size_of(item) for item in batch if item.notice_kind == KIND_MEMORY
        )
        if inputs.after_compaction:
            # 压缩后重注入的文件有自己的预算, 不占这一批的 20 KB.
            batch = (*batch, *self._recent_files(inputs))
        if batch:
            _log.info(
                "context.notices",
                kinds=[item.notice_kind for item in batch],
                after_compaction=inputs.after_compaction,
            )
        return batch

    # ---- 模式 (决策 4) ----

    def _mode(self, window: Window, mode: PermissionMode) -> list[ChatMessage]:
        last = _last_notice(window, KIND_MODE)
        if last is None or last[1].notice_revision != mode.value:
            out: list[ChatMessage] = []
            previous = _mode_of(last[1].notice_revision) if last is not None else None
            if previous is not None and previous in _ANNOUNCED_EXIT:
                exit_text = prompts.notice_mode_exit(previous)
                out.append(notice(KIND_MODE_EXIT, previous.value, exit_text))
            out.append(
                notice(KIND_MODE, mode.value, prompts.notice_mode(mode, full=True))
            )
            return out
        index, _ = last
        if _user_turns_since(window, index) < prompts.MODE_NOTICE_TURNS_BETWEEN:
            return []
        # 这是第几条同档的通知: 第 1, 6, 11 ... 条是完整版.
        streak = _trailing_streak(window, KIND_MODE, mode.value) + 1
        full = streak % prompts.MODE_NOTICE_FULL_EVERY == 1
        return [notice(KIND_MODE, mode.value, prompts.notice_mode(mode, full=full))]

    # ---- 计划与待办 (决策 5) ----

    def _plan(self, window: Window, inputs: NoticeInputs) -> list[ChatMessage]:
        plan = inputs.planning.live_plan
        if plan is None:
            return []
        revision = plan_revision(plan)
        if not self._changed_externally(window, KIND_PLAN, revision):
            return []
        return [
            notice(
                KIND_PLAN,
                revision,
                prompts.notice_plan(
                    plan_id=plan.plan_id,
                    title=plan.title,
                    status=plan.status.value,
                    step_count=plan.step_count,
                ),
            )
        ]

    def _todo(self, window: Window, inputs: NoticeInputs) -> list[ChatMessage]:
        todo = inputs.planning.live_todo
        if todo is None:
            return []
        revision = todo_revision(todo)
        if self._changed_externally(window, KIND_TODO, revision):
            return [
                notice(
                    KIND_TODO,
                    revision,
                    prompts.notice_todo(
                        rendered=todo.render(),
                        done=todo.done_count,
                        total=todo.total_count,
                    ),
                )
            ]
        # 提醒: 连续多次模型调用没碰过待办.
        anchor = max(
            _last_index(window, KIND_TODO),
            _last_index(window, KIND_TODO_REMINDER),
            _last_tool_call_index(window, TODO_TOOLS),
        )
        calls = _assistant_calls_since(window, anchor)
        if calls < prompts.TODO_REMINDER_CALLS_SINCE_WRITE:
            return []
        since_reminder = _assistant_calls_since(
            window, _last_index(window, KIND_TODO_REMINDER)
        )
        if since_reminder < prompts.TODO_REMINDER_CALLS_BETWEEN:
            return []
        return [
            notice(
                KIND_TODO_REMINDER,
                revision,
                prompts.notice_todo_reminder(todo.render()),
            )
        ]

    def _changed_externally(self, window: Window, kind: str, revision: str) -> bool:
        """这个版本模型还没见过: 既不是上一条通知说的, 也不是它自己工具结果里看过的.

        "看过"由 mark_seen 在每批工具结果之后记下, 而不是扫窗口找有没有调过对应工具:
        窗口里一条早先的 plan_write 说明不了后来的外部改动.
        """
        last = _last_notice(window, kind)
        if last is not None and last[1].notice_revision == revision:
            return False
        return self._seen.get(kind) != revision

    # ---- 记忆 ----

    def _memory(self, window: Window, inputs: NoticeInputs) -> list[ChatMessage]:
        current = memory_digest(inputs.memory)
        last = _last_notice(window, KIND_MEMORY)
        baseline = inputs.memory_baseline if last is None else last[1].notice_revision
        if current == baseline or self._seen.get(KIND_MEMORY) == current:
            return []
        text = prompts.truncate_notice(prompts.notice_memory(inputs.memory))
        size = len(text.encode("utf-8"))
        if self._memory_notice_bytes + size > prompts.MEMORY_NOTICE_SESSION_MAX_BYTES:
            _log.info("context.memory_notice_budget_exhausted")
            return []
        return [notice(KIND_MEMORY, current, text)]

    # ---- 工具池 (决策 8) ----

    def _tools(self, names: frozenset[str]) -> list[ChatMessage]:
        if self._announced_tools is None:
            self._announced_tools = names
            return []
        if names == self._announced_tools:
            return []
        added = sorted(names - self._announced_tools)
        removed = sorted(self._announced_tools - names)
        self._announced_tools = names
        revision = ",".join(sorted(names))
        return [
            notice(KIND_TOOLS, revision, prompts.notice_tools_delta(added, removed))
        ]

    # ---- 日期 (决策 7) ----

    @staticmethod
    def _date(window: Window, today: str, start_date: str) -> list[ChatMessage]:
        if today == start_date:
            return []
        last = _last_notice(window, KIND_DATE)
        if last is not None and last[1].notice_revision == today:
            return []
        return [notice(KIND_DATE, today, prompts.notice_date_change(today))]

    # ---- 子目录 FORGE.md (决策 7) ----

    def _nested_instructions(
        self, window: Window, inputs: NoticeInputs
    ) -> list[ChatMessage]:
        if inputs.read_files is None or not inputs.workspace_roots:
            return []
        announced = {
            message.notice_revision
            for message in window.messages
            if message.notice_kind == KIND_NESTED_INSTRUCTIONS
        }
        # 与 FileStateCache 的键走同一个规范化, 否则符号链接下的根永远对不上.
        roots = tuple(normalize_path(root) for root in inputs.workspace_roots)
        out: list[ChatMessage] = []
        for directory in _touched_directories(inputs.read_files.keys(), roots):
            target = os.path.join(directory, INSTRUCTION_FILE_NAME)
            if target in announced or not os.path.isfile(target):
                continue
            instruction = self._instructions.read_nested(directory)
            if instruction is None:
                continue
            announced.add(target)
            out.append(
                notice(
                    KIND_NESTED_INSTRUCTIONS,
                    target,
                    prompts.truncate_notice(
                        prompts.notice_nested_instructions(target, instruction.text)
                    ),
                )
            )
        return out

    # ---- 压缩后重注入最近读过的文件 (决策 9) ----

    def _recent_files(self, inputs: NoticeInputs) -> list[ChatMessage]:
        cache = inputs.read_files
        if cache is None:
            return []
        out: list[ChatMessage] = []
        budget = prompts.POST_COMPACT_TOKEN_BUDGET
        if inputs.file_token_room is not None:
            budget = min(budget, inputs.file_token_room)
        # keys() 按最近使用排, 最近的在末尾.
        for path in reversed(cache.keys()):
            if len(out) >= prompts.POST_COMPACT_MAX_FILES or budget <= 0:
                break
            state = cache.get(path)
            if state is None or not state.is_full_read or state.is_partial_view:
                continue
            try:
                current = Path(path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if current != state.content:
                # 压缩期间被改过: 发旧内容会误导, 让模型自己重读.
                continue
            limit_tokens = min(prompts.POST_COMPACT_MAX_TOKENS_PER_FILE, budget)
            content, truncated = _clip_tokens(current, limit_tokens)
            budget -= self._estimator.estimate_text(content)
            out.append(
                notice(
                    KIND_RECENT_FILE,
                    path,
                    prompts.notice_recent_file(path, content, truncated=truncated),
                )
            )
        return out


# ---- 窗口扫描 ----


def _last_notice(window: Window, kind: str) -> tuple[int, ChatMessage] | None:
    messages = window.messages
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].notice_kind == kind:
            return index, messages[index]
    return None


def _last_index(window: Window, kind: str) -> int:
    found = _last_notice(window, kind)
    return -1 if found is None else found[0]


def _last_tool_call_index(window: Window, names: frozenset[str]) -> int:
    messages = window.messages
    for index in range(len(messages) - 1, -1, -1):
        if any(call.name in names for call in messages[index].tool_calls):
            return index
    return -1


def _user_turns_since(window: Window, index: int) -> int:
    return sum(1 for message in window.messages[index + 1 :] if message.is_user_turn)


def _assistant_calls_since(window: Window, index: int) -> int:
    return sum(
        1
        for message in window.messages[index + 1 :]
        if message.role is MessageRole.ASSISTANT
    )


def _mode_of(value: str) -> PermissionMode | None:
    """回放的通知里可能带一个已经不存在的档位名, 那时当作没有上一条."""
    try:
        return PermissionMode(value)
    except ValueError:
        return None


def _size_of(message: ChatMessage) -> int:
    return sum(
        len(block.text.encode("utf-8"))
        for block in message.content
        if isinstance(block, TextBlock)
    )


def _trailing_streak(window: Window, kind: str, revision: str) -> int:
    """从末尾往前数同类型同 revision 的通知, 遇到不同 revision 的就停."""
    count = 0
    for message in reversed(window.messages):
        if message.notice_kind != kind:
            continue
        if message.notice_revision != revision:
            break
        count += 1
    return count


def _touched_directories(
    paths: Iterable[str], roots: tuple[str, ...]
) -> tuple[str, ...]:
    """读过的文件所在目录, 以及往上到工作区根 (不含根) 的每一级; 去重, 保序."""
    seen: list[str] = []
    for path in paths:
        directory = os.path.dirname(normalize_path(path))
        root = next(
            (r for r in roots if directory == r or directory.startswith(r + os.sep)),
            None,
        )
        if root is None:
            continue
        while directory != root:
            if directory not in seen:
                seen.append(directory)
            parent = os.path.dirname(directory)
            if parent == directory:
                break
            directory = parent
    return tuple(seen)


def _clip_tokens(text: str, limit_tokens: int) -> tuple[str, bool]:
    limit_chars = limit_tokens * _CHARS_PER_TOKEN
    if len(text) <= limit_chars:
        return text, False
    return text[:limit_chars], True


def _cap_batch(notices: list[ChatMessage]) -> tuple[ChatMessage, ...]:
    """单条截到 NOTICE_MAX_BYTES; 整批超过 NOTICE_BATCH_MAX_BYTES 时从末尾丢
    (排在后面的优先级低)."""
    kept: list[ChatMessage] = []
    total = 0
    for item in notices:
        text = "".join(
            block.text for block in item.content if isinstance(block, TextBlock)
        )
        clipped = prompts.truncate_notice(text)
        if clipped != text:
            item = notice(item.notice_kind, item.notice_revision, clipped)
        size = len(clipped.encode("utf-8"))
        if total + size > prompts.NOTICE_BATCH_MAX_BYTES:
            _log.warning("context.notice_batch_capped", dropped=item.notice_kind)
            continue
        total += size
        kept.append(item)
    return tuple(kept)
