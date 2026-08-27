"""回合内去重与文件变更通知 (ADR-0032 决策 3 / 4).

两件事共用一份判据 —— 每条工具结果自己记下的 (source_path, source_state) —— 所以放在
一个文件里测. 它们的分工是: 状态相同省一份正文, 状态不同主动报告文件变过了.

这里刻意**不**给预算. 去重与通知不是为了省空间, 是为了让模型知道"这份你读过"和"这个
文件后来被改了", 预算再宽也得跑.
"""

from __future__ import annotations

from forgecli.application.context.manager import ContextManager
from forgecli.domain.conversation.message import (
    ChatMessage,
    TextBlock,
    ToolResultBlock,
)
from forgecli.domain.conversation.turn import MessageRole
from forgecli.domain.tool.result import ResultProvenance
from support.fakes import MemoryArtifactStore


def _read_result(
    store: MemoryArtifactStore, *, call_id: str, path: str, state: str, body: str
) -> ChatMessage:
    ref = store.write(invocation_id=call_id, name="read_file", data=body)
    return ChatMessage(
        role=MessageRole.TOOL,
        content=(
            ToolResultBlock(
                tool_call_id=call_id,
                content=body,
                provenance=ResultProvenance(
                    artifact_id=ref.artifact_id,
                    source_path=path,
                    source_state=state,
                    byte_size=ref.size,
                ),
            ),
        ),
    )


def _write_result(*, call_id: str, paths: tuple[str, ...]) -> ChatMessage:
    return ChatMessage(
        role=MessageRole.TOOL,
        content=(
            ToolResultBlock(
                tool_call_id=call_id,
                content=f"已应用 {len(paths)} 处改动:",
                provenance=ResultProvenance(mutated_paths=paths),
            ),
        ),
    )


def _ask(text: str) -> ChatMessage:
    return ChatMessage(role=MessageRole.USER, content=(TextBlock(text),))


def _contents(messages: tuple[ChatMessage, ...]) -> list[str]:
    return [
        block.content
        for message in messages
        for block in message.content
        if isinstance(block, ToolResultBlock)
    ]


def test_reading_the_same_unchanged_file_twice_keeps_only_one_copy() -> None:
    store = MemoryArtifactStore()
    body = "def hello():\n    return 1\n"
    messages = (
        _ask("看看 main.py"),
        _read_result(store, call_id="c1", path="/ws/main.py", state="s1", body=body),
        _read_result(store, call_id="c2", path="/ws/main.py", state="s1", body=body),
    )

    result = ContextManager(artifacts=store).fit(
        messages, session_id="s1", turn_id="t1", budget=None
    )

    first, second = _contents(result.messages)
    assert first == body, "第一次读到的正文要原样留着"
    assert body not in second, "同一份内容不该在 transcript 里出现第二遍"
    assert "第 1 次工具调用" in second, "引用要指得回第一次"


def test_a_file_changed_between_two_reads_gets_an_unprompted_notice() -> None:
    """模型不会想起来问"这个文件变了吗", 所以必须主动告诉它."""
    store = MemoryArtifactStore()
    messages = (
        _ask("看看 main.py"),
        _read_result(
            store, call_id="c1", path="/ws/main.py", state="s1", body="旧内容"
        ),
        _read_result(
            store, call_id="c2", path="/ws/main.py", state="s2", body="新内容"
        ),
    )

    result = ContextManager(artifacts=store).fit(
        messages, session_id="s1", turn_id="t1", budget=None
    )

    stale, current = _contents(result.messages)
    assert "被修改过" in stale
    assert current == "新内容", "当前那一份要原样留着"


def test_the_older_record_is_downgraded_not_rewritten_as_the_new_content() -> None:
    """旧记录陈述的是"我在第 1 次调用时读到 X", 这在此刻依然为真, 它是历史."""
    store = MemoryArtifactStore()
    old_body = "旧内容"
    ref = store.write(invocation_id="c1", name="read_file", data=old_body)
    messages = (
        _read_result(store, call_id="c1", path="/ws/a.py", state="s1", body=old_body),
        _read_result(store, call_id="c2", path="/ws/a.py", state="s2", body="新内容"),
    )

    result = ContextManager(artifacts=store).fit(
        messages, session_id="s1", turn_id="t1", budget=None
    )

    stale = _contents(result.messages)[0]
    assert ref.artifact_id in stale, "取得回当初那一份, 只是它不再代表当前内容"


def test_shell_output_is_never_deduplicated() -> None:
    """它不是某个路径在某个状态下的快照, 重跑一次也不保证一样."""
    store = MemoryArtifactStore()
    body = "3 passed"
    ref = store.write(invocation_id="c1", name="output", data=body)
    run = ChatMessage(
        role=MessageRole.TOOL,
        content=(
            ToolResultBlock(
                tool_call_id="c1",
                content=body,
                provenance=ResultProvenance(artifact_id=ref.artifact_id),
            ),
        ),
    )
    messages = (run, run)

    result = ContextManager(artifacts=store).fit(
        messages, session_id="s1", turn_id="t1", budget=None
    )

    assert _contents(result.messages) == [body, body]


def test_running_fit_twice_changes_nothing_the_second_time() -> None:
    """循环每次调模型之前都会 fit 一遍, 不幂等就会一轮里反复改写同一条."""
    store = MemoryArtifactStore()
    body = "内容"
    messages = (
        _read_result(store, call_id="c1", path="/ws/a.py", state="s1", body=body),
        _read_result(store, call_id="c2", path="/ws/a.py", state="s1", body=body),
    )
    manager = ContextManager(artifacts=store)

    once = manager.fit(messages, session_id="s1", turn_id="t1", budget=None).messages
    twice = manager.fit(once, session_id="s1", turn_id="t1", budget=None).messages

    assert _contents(once) == _contents(twice)


def test_writing_a_file_marks_the_earlier_read_stale() -> None:
    """这条通知以前对写入完全不生效.

    判据只比对读与读: 模型改完一个文件, transcript 里那份改动前的内容仍然摆在那里,
    不带任何标记, 而它下一次拼 FIND 段照的就是那一份.
    """
    store = MemoryArtifactStore()
    body = "class SecurityConfig {\n}\n"
    messages = (
        _ask("看一下安全配置"),
        _read_result(
            store, call_id="c1", path="/w/SecurityConfig.java", state="s1", body=body
        ),
        _ask("加一个 UserDetailsService"),
        _write_result(call_id="c2", paths=("/w/SecurityConfig.java",)),
    )

    result = ContextManager(artifacts=store).fit(
        messages, session_id="s", turn_id="t", budget=None
    )

    assert body not in _contents(result.messages)
    assert "此后被修改过" in _contents(result.messages)[0]


def test_one_envelope_marks_every_file_it_touched() -> None:
    """一个信封可以动很多文件, 而 source_path 只装得下一个."""
    store = MemoryArtifactStore()
    messages = (
        _read_result(store, call_id="c1", path="/w/a.java", state="s1", body="A" * 40),
        _read_result(store, call_id="c2", path="/w/b.java", state="s1", body="B" * 40),
        _write_result(call_id="c3", paths=("/w/a.java", "/w/b.java")),
    )

    result = ContextManager(artifacts=store).fit(
        messages, session_id="s", turn_id="t", budget=None
    )

    assert all("此后被修改过" in text for text in _contents(result.messages)[:2])


def test_a_write_never_becomes_the_reference_target_of_a_later_read() -> None:
    """写入的正文是一行改动说明, 当不了"这份你读过"的引用目标.

    让它接任锚点的话, 模型顺着引用拿到的会是 "已应用 1 处改动", 而不是它要的那段代码.
    """
    store = MemoryArtifactStore()
    body = "class A {}\n"
    messages = (
        _write_result(call_id="c1", paths=("/w/a.java",)),
        _read_result(store, call_id="c2", path="/w/a.java", state="s2", body=body),
        _read_result(store, call_id="c3", path="/w/a.java", state="s2", body=body),
    )

    result = ContextManager(artifacts=store).fit(
        messages, session_id="s", turn_id="t", budget=None
    )

    contents = _contents(result.messages)
    assert contents[1] == body
    assert "与第 2 次工具调用读到的内容相同" in contents[2]
