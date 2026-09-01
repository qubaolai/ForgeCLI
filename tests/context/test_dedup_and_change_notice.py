"""回合内去重与文件变更通知 (ADR-0032 决策 3 / 4, 2026-09-01 修订).

两件事共用一份判据 —— 每条工具结果自己记下的 (source_path, source_state) —— 所以放在
一个文件里测. 它们的分工是: 状态相同省一份正文, 状态不同主动报告文件变过了.

这里刻意**不**给预算. 去重与通知不是为了省空间, 是为了让模型知道"这份你读过"和"这个
文件后来被改了", 预算再宽也得跑.

修订之后两者的**位置**不同, 这是本文件最要紧的一条:

- 去重改写的是**后一条**(靠近尾部), 缓存代价可以忽略;
- 变更通知改成**追加在新结果的末尾**, 不再回头改写前面那条读 —— 原地改写会让它之后的
  全部内容丢掉前缀缓存, 实测一次改文件多付 7,298 个未缓存 token, 而省下的只有把 4k
  正文换成 40 token 占位那点.
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


def test_a_changed_file_never_rewrites_the_earlier_record() -> None:
    """本文件最要紧的一条: 前面那条读原样不动.

    它陈述的是"我在第 1 次调用时读到 X", 这在此刻依然为真, 它是历史. 而更实际的理由是
    缓存: 改写 transcript 中段会让它之后的全部内容重新计费.
    """
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

    assert result.messages == messages, "同状态才去重; 状态不同时两条都原样留着"


def test_a_read_that_supersedes_an_earlier_one_says_so_at_the_tail() -> None:
    """通知本身没丢, 只是换了位置 —— 拼在新结果的末尾, 由循环回填时追加一次.

    这一条覆盖"文件被工作区之外的东西改了": 写入报不出来, 只有下一次读回来才知道.
    """
    store = MemoryArtifactStore()
    messages = (
        _read_result(
            store, call_id="c1", path="/ws/main.py", state="s1", body="旧内容"
        ),
    )
    incoming = ResultProvenance(source_path="/ws/main.py", source_state="s2")

    notice = ContextManager(artifacts=store).change_notice(messages, incoming)

    assert "第 1 次工具调用读到的 /ws/main.py" in notice
    assert "重新读取" in notice


def test_the_older_record_keeps_its_body_verbatim() -> None:
    """旧记录不降级也不改写. 它要被降级只有一个理由 —— 预算不够, 而那是另一条通路."""
    store = MemoryArtifactStore()
    messages = (
        _read_result(store, call_id="c1", path="/ws/a.py", state="s1", body="旧内容"),
        _read_result(store, call_id="c2", path="/ws/a.py", state="s2", body="新内容"),
    )

    result = ContextManager(artifacts=store).fit(
        messages, session_id="s1", turn_id="t1", budget=None
    )

    assert _contents(result.messages) == ["旧内容", "新内容"]


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
    """写入没有可比对的"下一次读", 所以它走 mutated_paths 直接说出自己动过谁."""
    store = MemoryArtifactStore()
    body = "class SecurityConfig {\n}\n"
    messages = (
        _ask("看一下安全配置"),
        _read_result(
            store, call_id="c1", path="/w/SecurityConfig.java", state="s1", body=body
        ),
        _ask("加一个 UserDetailsService"),
    )
    written = ResultProvenance(mutated_paths=("/w/SecurityConfig.java",))

    notice = ContextManager(artifacts=store).change_notice(messages, written)

    assert "第 1 次工具调用读到的 /w/SecurityConfig.java" in notice


def test_the_write_leaves_the_earlier_read_untouched() -> None:
    """通知发了, 但那份改动前的正文原样留在上面 —— 这正是缓存能保住的原因."""
    store = MemoryArtifactStore()
    body = "class SecurityConfig {\n}\n"
    messages = (
        _read_result(
            store, call_id="c1", path="/w/SecurityConfig.java", state="s1", body=body
        ),
        _write_result(call_id="c2", paths=("/w/SecurityConfig.java",)),
    )

    result = ContextManager(artifacts=store).fit(
        messages, session_id="s", turn_id="t", budget=None
    )

    assert result.messages == messages


def test_one_envelope_marks_every_file_it_touched() -> None:
    """一个信封可以动很多文件, 而 source_path 只装得下一个."""
    store = MemoryArtifactStore()
    messages = (
        _read_result(store, call_id="c1", path="/w/a.java", state="s1", body="A" * 40),
        _read_result(store, call_id="c2", path="/w/b.java", state="s1", body="B" * 40),
    )
    written = ResultProvenance(mutated_paths=("/w/a.java", "/w/b.java"))

    notice = ContextManager(artifacts=store).change_notice(messages, written)

    assert "/w/a.java" in notice
    assert "/w/b.java" in notice


def test_a_file_that_was_never_read_produces_no_notice() -> None:
    """说"你之前读的不作数了"是废话, 而废话会稀释真正要紧的那几句."""
    store = MemoryArtifactStore()
    messages = (
        _read_result(store, call_id="c1", path="/w/a.java", state="s1", body="A"),
    )
    written = ResultProvenance(mutated_paths=("/w/never-read.java",))

    assert ContextManager(artifacts=store).change_notice(messages, written) == ""


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
