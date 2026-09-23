import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from forgecli.agent.loop.builtin_loop import BuiltinAgentLoop
from forgecli.agent.loop.rules import builtin_rules
from forgecli.agent.run_bus import AgentRunEventBus, AgentRunEventSubscriber
from forgecli.agent.run_events import AgentRunEventKind, RunEventPayload
from forgecli.agent.sub_agent.runner import SubAgentResources, SubAgentRunner
from forgecli.agent.tool_observer import EventBusToolRunObserver
from forgecli.common.cancellation import CancelledError, CancelToken
from forgecli.core.agent.actions import LoopStop
from forgecli.core.agent.state import AssembledContext, ContextFingerprints, LoopInput
from forgecli.core.agent.stop import LoopStopReason
from forgecli.core.agent.sub_agent import (
    SubAgentIdentity,
    SubAgentLimitError,
    SubAgentStatus,
    SubAgentTask,
)
from forgecli.core.app_state import AppState
from forgecli.core.model.catalog import ModelCatalogEntry
from forgecli.core.model.origin import RequestOrigin
from forgecli.core.model.response import FinishReason, ModelUsage
from forgecli.core.model.streaming import ModelStreamChunk, ToolCallDelta
from forgecli.core.permissions import PermissionMode, ToolPermissionContext, allow
from forgecli.core.sandbox import SandboxSettings
from forgecli.core.tool.file_state import FileStateCache
from forgecli.core.tool.tool import PromptOptions
from forgecli.core.tool.tool_call import ToolCall
from forgecli.human_interaction.service import PendingHumanPromptService
from forgecli.llm.catalog import InMemoryModelCatalog
from forgecli.llm.gateway.gateway import LlmGateway
from forgecli.llm.metering import CostEstimator, UsageMeter
from forgecli.llm.transport_policy import ModelTransportPolicy
from forgecli.session.event_store import JsonlEventStore
from forgecli.session.service import SessionService
from forgecli.session.state_store import JsonStateStore
from forgecli.tools.builtin.Agent.tool import AgentTool
from forgecli.tools.builtin.Read.tool import FileReadTool
from forgecli.tools.runner import ToolRunner, sub_agent_runner


class Events(AgentRunEventSubscriber):
    def __init__(self):
        self.events = []

    def on_event(self, event):
        self.events.append(event)


class Gateway(LlmGateway):
    def __init__(self, responses=None, block=None):
        self.responses = responses or [("report", ())]
        self.requests = []
        self.block = block

    def stream(self, request):
        self.requests.append(request)
        if self.block:
            self.block(request)
        text, calls = self.responses[
            min(len(self.requests) - 1, len(self.responses) - 1)
        ]
        yield ModelStreamChunk(
            request.request_id,
            0,
            "fake",
            "test",
            delta_text=text,
            tool_call_deltas=tuple(
                ToolCallDelta(i, c.tool_call_id, c.name, json.dumps(dict(c.arguments)))
                for i, c in enumerate(calls)
            ),
        )
        yield ModelStreamChunk(
            request.request_id,
            1,
            "fake",
            "test",
            usage_delta=ModelUsage(input_tokens=10, output_tokens=5, total_tokens=15),
            finish_reason=FinishReason.TOOL_CALLS if calls else FinishReason.STOP,
        )

    def complete(self, request):
        raise NotImplementedError

    def complete_structured(self, request):
        raise NotImplementedError


def meter():
    return UsageMeter(
        CostEstimator(InMemoryModelCatalog([ModelCatalogEntry("fake", "test", 10000)]))
    )


def parent_tools(tmp_path, bus, mode=PermissionMode.DEFAULT, sandbox=False):
    state = AppState(
        ToolPermissionContext(mode=mode),
        SandboxSettings(enabled=sandbox, confined=sandbox),
    )
    return ToolRunner(
        base_tools=(FileReadTool(),),
        can_use_tool=lambda *a: allow(),
        get_app_state=lambda: state,
        set_app_state=lambda state: None,
        prompts=PendingHumanPromptService(),
        observer=EventBusToolRunObserver(bus),
        file_history=None,
        read_file_state=FileStateCache(),
        cwd=str(tmp_path),
        environment={},
        workspace_roots=(str(tmp_path),),
        tool_results_dir=lambda: str(tmp_path / "tool-results"),
        internal_paths=(str(tmp_path),),
    )


def harness(tmp_path, gateway, **limits):
    bus = AgentRunEventBus()
    events = Events()
    bus.subscribe(events)
    parent = parent_tools(tmp_path, bus)
    usage = []

    def prepare(identity, cancel, limit):
        tools = sub_agent_runner(parent, identity, EventBusToolRunObserver(bus))
        loop = BuiltinAgentLoop(
            gateway,
            meter(),
            rules=builtin_rules(context=None, max_model_calls=limit),
            model_transport_policy=ModelTransportPolicy(),
            cancel_token_factory=lambda: cancel,
            event_bus=bus,
        )
        context = AssembledContext(
            "read only", ContextFingerprints("a", "b", "c"), tools=tools.tool_schemas()
        )
        return SubAgentResources(
            loop,
            tools,
            context,
            tmp_path / "agents" / f"{identity.agent_id}.json",
            usage.append,
        )

    runner = SubAgentRunner(bus=bus, prepare=prepare, **limits)
    bus.subscribe(runner)
    context = parent.context(
        session_id="session", turn_id="turn", messages=(), cancel=CancelToken()
    )
    context.tool_use_id = "dispatch"
    return runner, context, parent, usage, events


def test_delegation_uses_loop_reads_and_returns_report(tmp_path):
    target = tmp_path / "source.py"
    target.write_text("value = 42\n")
    gateway = Gateway(
        [
            ("reading", (ToolCall("read1", "Read", {"file_path": str(target)}),)),
            ("证据 source.py:1 value = 42", ()),
        ]
    )
    runner, context, parent, usage, events = harness(tmp_path, gateway)
    report = runner.run(SubAgentTask("read code", "Find value"), context)
    assert report.status is SubAgentStatus.COMPLETED
    assert "42" in report.text
    assert len(usage) == 2
    assert all(
        d.agent_id == report.identity.agent_id and d.origin is RequestOrigin.SUB_AGENT
        for d in usage
    )
    assert not parent.read_file_state.has(str(target))
    assert all(e.agent_id == report.identity.agent_id for e in events.events)
    assert not any(e.kind.value.startswith("turn_") for e in events.events)
    assert [e.kind for e in events.events].count(
        AgentRunEventKind.SUB_AGENT_FINISHED
    ) == 1
    saved = json.loads(
        (tmp_path / "agents" / f"{report.identity.agent_id}.json").read_text()
    )
    assert "value = 42" in json.dumps(saved["window"])
    assert gateway.requests[0].cancel_token is not context.abort


def test_parent_loop_dispatch_and_resume(tmp_path):
    child_gateway = Gateway()
    runner, context, parent, usage, events = harness(tmp_path, child_gateway)
    parent.base_tools += (AgentTool(runner.run),)
    root_gateway = Gateway(
        [
            (
                "delegating",
                (
                    ToolCall(
                        "agentcall",
                        "Agent",
                        {"description": "research", "prompt": "read"},
                    ),
                ),
            ),
            ("final root answer", ()),
        ]
    )
    loop = BuiltinAgentLoop(
        root_gateway,
        meter(),
        rules=builtin_rules(context=None),
        model_transport_policy=ModelTransportPolicy(),
        event_bus=runner._bus,
        cancel_token_factory=lambda: context.abort,
    )
    from forgecli.tools.runner import observation_of

    result = loop.run(
        LoopInput("turn", "session", PermissionMode.DEFAULT),
        assemble=lambda window: AssembledContext(
            "root", ContextFingerprints("a", "b", "c"), tools=parent.tool_schemas()
        ),
        execute=lambda calls: tuple(
            observation_of(o)
            for o in parent.run(
                calls, session_id="session", turn_id="turn", cancel=context.abort
            )
        ),
    )
    assert result.answer == "final root answer"
    assert len(usage) == 1
    assert sum(e.kind is AgentRunEventKind.TURN_COMPLETED for e in events.events) == 1
    assert "report" in str(root_gateway.requests[-1].messages)


def test_cancel_tree():
    parent = CancelToken()
    child = parent.child()
    sibling = parent.child()
    child.cancel()
    assert not parent.cancelled and not sibling.cancelled
    parent.cancel()
    assert sibling.cancelled


def test_timeout_waits_for_actual_exit_and_holds_slot(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def block(request):
        entered.set()
        assert release.wait(5)

    runner, context, _, usage, events = harness(
        tmp_path, Gateway(block=block), timeout=0.02, grace=0.02
    )
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(runner.run, SubAgentTask("slow", "read"), context)
        assert entered.wait(2)
        # Wait on the observable stopping event, not a fixed long timeout.
        stopping = threading.Event()

        class Stopped(AgentRunEventSubscriber):
            def on_event(self, event):
                if event.kind is AgentRunEventKind.SUB_AGENT_STOPPING:
                    stopping.set()

        runner._bus.subscribe(Stopped())
        try:
            assert stopping.wait(2)
            assert not future.done()
            assert not runner._slots.acquire(blocking=False)
            assert not any(
                e.kind is AgentRunEventKind.SUB_AGENT_FINISHED for e in events.events
            )
        finally:
            release.set()
        assert future.result(2).status is SubAgentStatus.TIMED_OUT
    assert len(usage) == 1


def test_parent_cancel_settles_child_before_raising(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def block(request):
        entered.set()
        assert release.wait(3)
        assert request.cancel_token.cancelled

    runner, context, _, usage, events = harness(tmp_path, Gateway(block=block))
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(runner.run, SubAgentTask("cancel", "read"), context)
        assert entered.wait(2)
        context.abort.cancel()
        release.set()
        with pytest.raises(CancelledError):
            future.result(2)
    assert events.events[-1].payload.status == "cancelled"
    assert runner._slots.acquire(blocking=False)


def test_budget_and_atomic_dispatch_limit(tmp_path):
    gateway = Gateway(
        [
            (
                "reading",
                (ToolCall("r", "Read", {"file_path": str(tmp_path / "missing")}),),
            )
        ]
    )
    runner, context, *_ = harness(
        tmp_path, gateway, model_calls=1, limit=1, concurrency=2
    )
    with ThreadPoolExecutor(2) as pool:
        futures = [
            pool.submit(runner.run, SubAgentTask("read", "read"), context)
            for _ in range(2)
        ]
        results = []
        for f in futures:
            try:
                results.append(f.result(3))
            except SubAgentLimitError:
                results.append(None)
    assert results.count(None) == 1
    assert next(r for r in results if r).status is SubAgentStatus.BUDGET_EXHAUSTED
    assert len(gateway.requests) == 1


def test_event_order_including_reentrant_publish():
    bus = AgentRunEventBus()
    collected = Events()

    class Reentrant(AgentRunEventSubscriber):
        def on_event(self, event):
            if event.sequence == 1:
                bus.publish(
                    AgentRunEventKind.DECISION_SUMMARY,
                    session_id="s",
                    turn_id="t",
                    payload=RunEventPayload(),
                )

    bus.subscribe(Reentrant())
    bus.subscribe(collected)
    with ThreadPoolExecutor(8) as pool:
        list(
            pool.map(
                lambda _: bus.publish(
                    AgentRunEventKind.DECISION_SUMMARY,
                    session_id="s",
                    turn_id="t",
                    payload=RunEventPayload(),
                ),
                range(100),
            )
        )
    bus.flush()
    assert [e.sequence for e in collected.events] == list(range(1, 102))


def test_concurrent_session_usage_and_identity(tmp_path):
    store = JsonlEventStore(tmp_path)
    session = SessionService(
        store, JsonStateStore(tmp_path), workspace_root=str(tmp_path), gateway=Gateway()
    )
    session.start()
    sid = session.current().session_id
    with ThreadPoolExecutor(8) as pool:
        list(
            pool.map(
                lambda i: session.record_usage(
                    {"session_id": sid, "request_id": str(i)}, turn_id="t"
                ),
                range(50),
            )
        )
    events = store.read(sid)
    assert len(events) == 51
    assert len({e.event_id for e in events}) == 51
    assert session.current().last_event_id == events[-1].event_id
    state = json.loads((tmp_path / sid / "state.json").read_text())
    assert state["last_event_id"] == events[-1].event_id
    with pytest.raises(Exception, match="different session"):
        session.record_usage({"session_id": "wrong"}, turn_id="t")


@pytest.mark.parametrize("mode", list(PermissionMode))
@pytest.mark.parametrize("sandbox", [False, True])
def test_child_permission_view_never_inherits_elevation(tmp_path, mode, sandbox):
    bus = AgentRunEventBus()
    parent = parent_tools(tmp_path, bus, mode, sandbox)
    child = sub_agent_runner(
        parent,
        SubAgentIdentity("s", "t", "child", "call"),
        EventBusToolRunObserver(bus),
    )
    state = child.get_app_state()
    assert state.tool_permission_context.mode is PermissionMode.DEFAULT
    assert state.tool_permission_context.should_avoid_permission_prompts
    assert not state.tool_permission_context.always_allow_rules
    assert not state.sandbox.auto_allow_bash_if_sandboxed
    child.set_app_state(replace(state, sandbox=SandboxSettings()))
    assert parent.get_app_state().sandbox.active == sandbox


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf target",
        "mv target other",
        "echo changed > target",
        "python -c 'print(1)'",
        "git log > target",
        "git branch newbranch",
        "git branch -- -l",
        "git tag newtag",
        "git tag -a newtag",
        "git config user.name Example",
        "git stash",
        "uniq target other",
        "npm version 1.2.3",
    ],
)
@pytest.mark.parametrize("sandbox", [False, True])
def test_final_readonly_guard_defeats_allow_decision(tmp_path, command, sandbox):
    from forgecli.core.execution.environment import ShellLaunch
    from forgecli.core.tool.tool import ToolResult
    from forgecli.tools.builtin.Bash.tool import BashTool
    from forgecli.tools.execution import run_tool_use

    called = []

    class SpyBash(BashTool):
        def call(self, *args, **kwargs):
            called.append(True)
            return ToolResult(data="should not run")

    bash = SpyBash(
        local=None,
        sandboxed=None,
        shell_launch=ShellLaunch("/bin/sh", ("-c",), "posix"),
        fence_factory=None,
    )
    bus = AgentRunEventBus()
    parent = parent_tools(tmp_path, bus, PermissionMode.BYPASS_PERMISSIONS, sandbox)
    parent.base_tools += (bash,)
    child = sub_agent_runner(
        parent, SubAgentIdentity("s", "t", "a", "call"), EventBusToolRunObserver(bus)
    )
    context = child.context(
        session_id="s", turn_id="t", messages=(), cancel=CancelToken()
    )
    (tmp_path / "target").write_text("original")
    result = run_tool_use(
        ToolCall("x", "Bash", {"command": command}), context, lambda *a: allow(), None
    )
    assert result.status == "denied"
    assert not called
    assert (tmp_path / "target").read_text() == "original"


def test_real_bash_permission_with_active_sandbox_is_readonly(tmp_path):
    from forgecli.core.execution.environment import ShellLaunch
    from forgecli.tools.builtin.Bash.tool import BashTool

    bash = BashTool(
        local=None,
        sandboxed=None,
        shell_launch=ShellLaunch("/bin/sh", ("-c",), "posix"),
        fence_factory=None,
    )
    bus = AgentRunEventBus()
    parent = parent_tools(tmp_path, bus, PermissionMode.BYPASS_PERMISSIONS, True)
    parent.base_tools += (bash,)
    child = sub_agent_runner(
        parent, SubAgentIdentity("s", "t", "a", "call"), EventBusToolRunObserver(bus)
    )
    context = child.context(
        session_id="s", turn_id="t", messages=(), cancel=CancelToken()
    )
    assert not bash.check_permissions({"command": "rm -rf target"}, context).is_allow
    assert bash.check_permissions({"command": "git log"}, context).is_allow


def test_compact_request_keeps_identity_cancel_and_selected_window():
    from forgecli.conversation.summarize import summarize
    from forgecli.core.context.window import EvictionPlan
    from forgecli.core.message import ChatMessage, TextBlock
    from forgecli.core.model.model_ref import ModelRef
    from forgecli.core.model.resolved import ResolvedModel
    from forgecli.core.model.response import ModelResponse
    from forgecli.core.token_estimator import ApproximateTokenEstimator
    from forgecli.core.turn import MessageRole

    class Compact(Gateway):
        def complete(self, request):
            self.requests.append(request)
            return ModelResponse(
                request.request_id,
                "fake",
                "test",
                "summary",
                FinishReason.STOP,
                ModelUsage(input_tokens=10, output_tokens=5, total_tokens=15),
                1,
            )

    gateway = Compact()
    cancel = CancelToken().child()
    model = ResolvedModel(
        ModelRef("fake", "test"),
        ModelCatalogEntry("fake", "test", 2000, max_output_tokens=100),
    )
    dropped = (ChatMessage(MessageRole.ASSISTANT, (TextBlock("内容" * 10000),)),)
    _, summary = summarize(
        EvictionPlan(1, dropped, (), ()),
        gateway,
        session_id="s",
        turn_id="t",
        agent_id="a",
        cancel_token=cancel,
        resolved_model=model,
        meter=meter(),
    )
    request = gateway.requests[0]
    assert request.origin is RequestOrigin.COMPACT
    assert request.agent_id == "a" and request.cancel_token is cancel
    assert request.resolved_model is model
    assert ApproximateTokenEstimator().estimate_input(messages=request.messages) < 1900
    assert summary.usage.agent_id == "a"


def test_overflow_compaction_receives_child_cancel():
    from forgecli.agent.loop.event_publisher import LoopEventPublisher
    from forgecli.agent.loop.ledger import TurnLedger
    from forgecli.agent.loop.rule import LoopView
    from forgecli.agent.loop.rules.context_fit import ContextFitRule
    from forgecli.conversation.window_manager import WindowFitResult
    from forgecli.core.agent.phase import LoopPhase
    from forgecli.core.context.budget import ContextBudget
    from forgecli.core.context.window import Window
    from forgecli.llm.gateway.errors import ModelContextOverflowError

    class WindowSpy:
        def __init__(self):
            self.calls = []

        def fit(self, window, **kwargs):
            self.calls.append(kwargs)
            return WindowFitResult(window)

    window = WindowSpy()
    token = CancelToken().child()
    view = LoopView(
        "s",
        "t",
        PermissionMode.DEFAULT,
        LoopPhase.RUNNING,
        Window(),
        (),
        False,
        ContextBudget(1000),
        "prompt",
        0,
        0,
        0,
        TurnLedger(LoopEventPublisher(None)),
        agent_id="a",
        cancel_token=token,
    )
    rule = ContextFitRule(window)
    rule.before_model(view)
    rule.on_overflow(ModelContextOverflowError("overflow"), view)
    assert len(window.calls) == 2
    assert all(
        c["cancel_token"] is token and c["agent_id"] == "a" for c in window.calls
    )


def test_exit_snapshot_keeps_usage_on_unexpected_failure(tmp_path):
    gateway = Gateway([("dispatch", (ToolCall("x", "Read", {"file_path": "x"}),))])
    loop = BuiltinAgentLoop(
        gateway,
        meter(),
        rules=builtin_rules(context=None),
        model_transport_policy=ModelTransportPolicy(),
    )

    def fail(calls):
        raise RuntimeError("failed tool bridge")

    with pytest.raises(RuntimeError):
        loop.run(
            LoopInput("t", "s", PermissionMode.DEFAULT, agent_id="a"),
            execute=fail,
            assemble=lambda window: AssembledContext(
                "research",
                ContextFingerprints("a", "b", "c"),
                tools=(FileReadTool().to_model_schema(PromptOptions()),),
            ),
        )
    snapshot = loop.exit_snapshot(LoopStop(LoopStopReason.MODEL_ERROR_BLOCKING))
    assert len(snapshot.usage_drafts) == 1 and snapshot.model_calls == 1


def test_child_result_directories_do_not_collide(tmp_path):
    from forgecli.tools.result_storage import persist_if_needed

    bus = AgentRunEventBus()
    parent = parent_tools(tmp_path, bus)

    class SmallRead(FileReadTool):
        max_result_size_chars = 5

    contents = []
    for aid, text in [("a", "aaaaaaaaaa"), ("b", "bbbbbbbbbb")]:
        child = sub_agent_runner(
            parent,
            SubAgentIdentity("s", "t", aid, "call"),
            EventBusToolRunObserver(bus),
        )
        ctx = child.context(
            session_id="s", turn_id="t", messages=(), cancel=CancelToken()
        )
        _, saved = persist_if_needed(
            text, SmallRead(), tool_use_id="same", tool_results_dir=ctx.tool_results_dir
        )
        assert saved
        contents.append(Path(ctx.tool_results_dir) / "same.txt")
    assert contents[0] != contents[1]
    assert [p.read_text() for p in contents] == ["aaaaaaaaaa", "bbbbbbbbbb"]


def test_parallel_agent_tools_overlap_and_keep_request_order(tmp_path):
    barrier = threading.Barrier(3)
    gateway = Gateway(block=lambda request: barrier.wait(3))
    runner, context, parent, usage, events = harness(tmp_path, gateway, concurrency=3)
    parent.base_tools += (AgentTool(runner.run),)
    calls = tuple(
        ToolCall(f"a{i}", "Agent", {"description": f"task{i}", "prompt": "read"})
        for i in range(3)
    )
    outcomes = parent.run(
        calls, session_id="session", turn_id="turn", cancel=context.abort
    )
    assert [o.block.tool_call_id for o in outcomes] == [c.tool_call_id for c in calls]
    assert all(not o.block.is_error for o in outcomes)
    assert len({d.agent_id for d in usage}) == 3
    starts = [e for e in events.events if e.kind is AgentRunEventKind.SUB_AGENT_STARTED]
    assert {e.payload.parent_tool_call_id for e in starts} == {
        c.tool_call_id for c in calls
    }
    assert [e.sequence for e in events.events] == sorted(
        {e.sequence for e in events.events}
    )


def test_cancelled_waiter_does_not_start(tmp_path):
    entered, release = threading.Event(), threading.Event()

    def block(request):
        entered.set()
        assert release.wait(3)

    runner, context, _, _, events = harness(
        tmp_path, Gateway(block=block), concurrency=1
    )
    second = replace(context, abort=CancelToken())
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(runner.run, SubAgentTask("one", "read"), context)
        assert entered.wait(2)
        waiter = pool.submit(runner.run, SubAgentTask("two", "read"), second)
        second.abort.cancel()
        try:
            with pytest.raises(CancelledError):
                waiter.result(2)
        finally:
            release.set()
        first.result(2)
    assert (
        sum(e.kind is AgentRunEventKind.SUB_AGENT_STARTED for e in events.events) == 1
    )


def test_cooldown_store_parallel_updates_preserve_all_keys(tmp_path):
    from forgecli.llm.gateway.credentials import CredentialCooldownStore

    store = CredentialCooldownStore(tmp_path / "cooldowns.json")
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda i: store.set(str(i), 100, now=0), range(50)))
    assert len(store.load(now=0)) == 50


def test_circuit_allows_only_one_half_open_probe():
    from forgecli.core.model.model_ref import ModelRef
    from forgecli.llm.gateway.errors import ModelUnavailableError
    from forgecli.llm.gateway.governance import SlidingWindowHealthRegistry

    now = [0]
    registry = SlidingWindowHealthRegistry(
        failure_threshold=1, cooldown_seconds=1, clock=lambda: now[0]
    )
    ref = ModelRef("fake", "test")
    registry.record_failure(ref)
    now[0] = 2
    barrier = threading.Barrier(8)

    def check(_):
        barrier.wait(2)
        try:
            registry.check(ref)
            return True
        except ModelUnavailableError:
            return False

    with ThreadPoolExecutor(8) as pool:
        assert sum(pool.map(check, range(8))) == 1


def test_prepare_pins_child_model_and_context_budget(tmp_path):
    from types import SimpleNamespace

    from forgecli.core.model.model_ref import ModelRef
    from forgecli.core.model.resolved import ResolvedModel
    from forgecli.prompt.session_facts import SessionFacts
    from forgecli.runtime.sub_agent_wiring import prepare_sub_agent

    bus = AgentRunEventBus()
    parent = parent_tools(tmp_path, bus)
    session = SessionService(
        JsonlEventStore(tmp_path), JsonStateStore(tmp_path), str(tmp_path), Gateway()
    )
    session.start()
    small = ResolvedModel(
        ModelRef("fake", "test"),
        ModelCatalogEntry("fake", "test", 6000, max_output_tokens=100),
    )
    huge = replace(small, entry=replace(small.entry, context_window=200000))
    selections = {RequestOrigin.SUB_AGENT: small, RequestOrigin.COMPACT: huge}
    llm = SimpleNamespace(
        gateway=Gateway(),
        usage_meter=meter(),
        resolve_model=lambda *, origin: selections[origin],
    )
    identity = SubAgentIdentity(session.current().session_id, "t", "a", "c")
    token = CancelToken().child()
    resources = prepare_sub_agent(
        identity,
        token,
        30,
        llm=llm,
        parent=parent,
        session=session,
        bus=bus,
        facts=SessionFacts(
            "test",
            "posix",
            str(tmp_path),
            (str(tmp_path),),
            False,
            False,
            "fake:test",
            "2026-09-21",
        ),
    )
    selections[RequestOrigin.SUB_AGENT] = huge
    assert resources.context.budget.context_window == 6000
    resources.loop.run(
        LoopInput("t", identity.session_id, PermissionMode.DEFAULT, agent_id="a"),
        assemble=lambda window: resources.context,
        execute=lambda calls: (),
    )
    assert llm.gateway.requests[0].resolved_model == small
    assert llm.gateway.requests[0].cancel_token is token


def test_usage_write_failure_is_visible_and_releases_slot(tmp_path):
    runner, context, *_ = harness(tmp_path, Gateway())
    original = runner._prepare

    def fail(draft):
        raise OSError("disk full")

    runner._prepare = lambda *args: replace(original(*args), record_usage=fail)
    report = runner.run(SubAgentTask("read", "read"), context)
    assert report.status is SubAgentStatus.FAILED
    assert not report.usage_complete and "disk full" in report.text
    assert runner._slots.acquire(blocking=False)


def test_parent_cancel_during_settlement_changes_child_terminal_status(tmp_path):
    runner, context, _, _, events = harness(tmp_path, Gateway())
    prepare = runner._prepare

    def settle(draft):
        context.abort.cancel()

    runner._prepare = lambda *args: replace(prepare(*args), record_usage=settle)
    with pytest.raises(CancelledError):
        runner.run(SubAgentTask("read", "read"), context)
    assert events.events[-1].payload.status == "cancelled"


def test_three_running_children_all_observe_parent_cancel(tmp_path):
    barrier = threading.Barrier(4)
    release = threading.Event()
    seen = []

    def block(request):
        barrier.wait(3)
        assert release.wait(3)
        seen.append(request.cancel_token.cancelled)

    runner, context, _, _, events = harness(
        tmp_path, Gateway(block=block), concurrency=3
    )
    with ThreadPoolExecutor(3) as pool:
        futures = [
            pool.submit(runner.run, SubAgentTask("read", "read"), context)
            for _ in range(3)
        ]
        try:
            barrier.wait(3)
            context.abort.cancel()
        finally:
            release.set()
        for f in futures:
            with pytest.raises(CancelledError):
                f.result(3)
    assert seen == [True] * 3
    finished = [
        e for e in events.events if e.kind is AgentRunEventKind.SUB_AGENT_FINISHED
    ]
    assert len(finished) == 3 and all(e.payload.status == "cancelled" for e in finished)


def test_thread_start_failure_releases_slot_without_fake_lifecycle(
    tmp_path, monkeypatch
):
    runner, context, _, _, events = harness(tmp_path, Gateway(), limit=1)

    def fail(self):
        raise RuntimeError("thread creation failed")

    monkeypatch.setattr(threading.Thread, "start", fail)
    with pytest.raises(RuntimeError, match="thread creation"):
        runner.run(SubAgentTask("read", "read"), context)
    assert not events.events
    assert runner._slots.acquire(blocking=False)
    with pytest.raises(SubAgentLimitError):
        runner.run(SubAgentTask("read", "read"), context)


def test_terminal_view_keeps_child_text_separate_and_counts_usage():
    from io import StringIO

    from rich.console import Console

    from forgecli.agent.run_events import (
        ModelUsagePayload,
        SubAgentFinishedPayload,
        TextDeltaPayload,
    )
    from forgecli.tui.run_view import TerminalRunView

    bus = AgentRunEventBus()
    events = Events()
    bus.subscribe(events)
    output = StringIO()
    view = TerminalRunView(Console(file=output, width=120))
    for kind, payload in [
        (
            AgentRunEventKind.MODEL_OUTPUT_DELTA,
            TextDeltaPayload(text="child private narration"),
        ),
        (
            AgentRunEventKind.MODEL_USAGE,
            ModelUsagePayload(input_tokens=10, output_tokens=5, total_tokens=15),
        ),
        (
            AgentRunEventKind.SUB_AGENT_FINISHED,
            SubAgentFinishedPayload("completed", 1, 0, 15, 10),
        ),
    ]:
        view.handle(
            bus.publish(
                kind, session_id="s", turn_id="t", agent_id="a", payload=payload
            )
        )
    assert "child private narration" not in output.getvalue()
    assert view.metrics.total_tokens == 15 and not view.finished


@pytest.fixture
def bash_contexts(tmp_path):
    from forgecli.core.execution.environment import ShellLaunch
    from forgecli.execution.local_command_executor import LocalCommandExecutor
    from forgecli.tools.builtin.Bash.tool import BashTool

    (tmp_path / "src" / "nested").mkdir(parents=True)
    (tmp_path / "src" / "space dir").mkdir()
    (tmp_path / "src" / "sample.txt").write_text("needle\n")
    bash = BashTool(
        local=LocalCommandExecutor(),
        sandboxed=None,
        shell_launch=ShellLaunch("/bin/bash", ("--noprofile", "--norc", "-c"), "posix"),
        fence_factory=None,
    )
    bus = AgentRunEventBus()
    parent = parent_tools(tmp_path, bus)
    parent.base_tools += (bash,)
    parent.environment = {"PATH": "/usr/bin:/bin"}
    child = sub_agent_runner(
        parent, SubAgentIdentity("s", "t", "a", "call"), EventBusToolRunObserver(bus)
    )
    return bash, tuple(
        runner.context(session_id="s", turn_id="t", messages=(), cancel=CancelToken())
        for runner in (parent, child)
    )


@pytest.mark.parametrize(
    "command",
    [
        "ls -la 2>/dev/null",
        "ls src 2>>/dev/null; ls src/nested 2>/dev/null | head -60",
        "grep -n needle src/sample.txt >/dev/null 2>&1",
        "cd src && grep -n needle sample.txt 2>/dev/null",
        "cd -- src && ls 2>/dev/null",
        "cd src && cd nested && ls",
        "cd src && cd .. && ls",
        'cd src && cd "space dir" && ls',
        "cd src && grep -n needle sample.txt | sort",
        "cd src; ls",
        "cd src || ls",
        "cd src && ls && cd .. && ls",
        "cd src && grep -n absent sample.txt 2>/dev/null | head -50; "
        "echo '--- dirs ---'; ls -la",
        "cd src\nls -la",
        "cd src && sed -n '1,60p' sample.txt",
    ],
)
def test_static_cd_and_null_output_execute_in_parent_and_child(bash_contexts, command):
    from forgecli.security.permissions import has_permissions_to_use_tool
    from forgecli.tools.execution import run_tool_use

    bash, contexts = bash_contexts
    assert bash.is_read_only({"command": command})
    for context in contexts:
        result = run_tool_use(
            ToolCall("x", "Bash", {"command": command}),
            context,
            lambda tool, input, ctx, *_: has_permissions_to_use_tool(tool, input, ctx),
            None,
        )
        assert result.status == "ok", result
        assert result.executed
    assert contexts[0].cwd == contexts[1].cwd


@pytest.mark.parametrize(
    "command",
    [
        "cd .. && ls",
        "cd missing && ls",
        "cd - && ls",
        "cd && ls",
        "cd -P src && ls",
        "cd $HOME && ls",
        "cd src* && ls",
        "cd src & ls",
        "cd src | ls",
        "(cd src) && ls",
        "if cd src; then ls; fi",
        "ls 2> errors.txt",
        "ls >/dev/null 2> errors.txt",
        "ls >/dev/null >&errors.txt",
        "ls >/dev/null/../file",
        "ls >dev/null",
        "ls >$SINK",
        "ls > /dev/zero",
        "cd src && rm sample.txt >/dev/null",
        "cd src && find . -exec rm '{}' ';' >/dev/null",
    ],
)
def test_unsafe_or_ambiguous_commands_still_require_permission(bash_contexts, command):
    from forgecli.security.permissions import has_permissions_to_use_tool

    bash, (parent, child) = bash_contexts
    assert has_permissions_to_use_tool(bash, {"command": command}, parent).is_ask
    assert has_permissions_to_use_tool(bash, {"command": command}, child).is_deny


def test_cd_checks_symlinks_and_cdpath(bash_contexts, tmp_path):
    from forgecli.security.permissions import has_permissions_to_use_tool

    bash, contexts = bash_contexts
    (tmp_path / "outside").symlink_to(tmp_path.parent, target_is_directory=True)
    for context in contexts:
        for command in ("cd outside && ls", "cd src && cd ../../ && ls"):
            assert not has_permissions_to_use_tool(
                bash, {"command": command}, context
            ).is_allow
        with_cdpath = replace(context, environment={"CDPATH": str(tmp_path.parent)})
        assert not has_permissions_to_use_tool(
            bash, {"command": "cd src && ls"}, with_cdpath
        ).is_allow
        assert has_permissions_to_use_tool(
            bash, {"command": "cd ./src && ls"}, with_cdpath
        ).is_allow


def test_following_paths_are_checked_from_changed_directory(bash_contexts, tmp_path):
    from forgecli.security.permissions import has_permissions_to_use_tool

    bash, (parent, _) = bash_contexts
    state = replace(
        parent.get_app_state(),
        tool_permission_context=ToolPermissionContext(mode=PermissionMode.ACCEPT_EDITS),
    )
    context = replace(parent, get_app_state=lambda: state)
    assert has_permissions_to_use_tool(
        bash, {"command": "cd src && touch ../inside.txt"}, context
    ).is_allow
    assert not has_permissions_to_use_tool(
        bash, {"command": "cd src && touch ../../outside.txt"}, context
    ).is_allow
    # From src/nested this link escapes the project; from the initial cwd it
    # would appear to name an innocuous in-project path.
    (tmp_path / "src" / "escape").symlink_to(tmp_path.parent, target_is_directory=True)
    result = has_permissions_to_use_tool(
        bash,
        {"command": "cd src && cd nested && echo hi > ../escape/out.txt"},
        context,
    )
    assert result.is_ask


def test_null_sink_must_be_a_character_device(monkeypatch):
    from types import SimpleNamespace

    from forgecli.tools.builtin.Bash import static_paths

    monkeypatch.setattr(
        static_paths.os, "stat", lambda _: SimpleNamespace(st_mode=0o100644)
    )
    assert not static_paths.is_null_sink("/dev/null")


@pytest.mark.parametrize("rule", ["Bash(ls:*)", "Bash(cd:*)"])
@pytest.mark.parametrize("behavior", ["deny", "ask"])
def test_static_bash_does_not_override_explicit_rules(bash_contexts, rule, behavior):
    from forgecli.core.permissions import PermissionRuleSource
    from forgecli.security.permissions import has_permissions_to_use_tool

    bash, contexts = bash_contexts
    for context in contexts:
        state = context.get_app_state()
        rules = {PermissionRuleSource.SESSION: (rule,)}
        permission = replace(
            state.tool_permission_context,
            always_deny_rules=rules if behavior == "deny" else {},
            always_ask_rules=rules if behavior == "ask" else {},
        )
        restricted_state = replace(state, tool_permission_context=permission)
        restricted = replace(
            context, get_app_state=lambda state=restricted_state: state
        )
        result = has_permissions_to_use_tool(
            bash, {"command": "cd src && ls 2>/dev/null"}, restricted
        )
        assert not result.is_allow
        if context.agent_id or behavior == "deny":
            assert result.is_deny
        else:
            assert result.is_ask


@pytest.mark.parametrize(
    "command",
    [
        "git branch newbranch",
        "git tag newtag",
        "git config user.name Example",
        "git branch -- -l",
        "git tag -- -l",
        "git stash",
        "git diff --output=out",
        "cd src && git status",
        "sed -n '1p;w out' src/sample.txt",
        "sed -n '1e' src/sample.txt",
        "uniq src/sample.txt out",
        "npm version 1.2.3",
    ],
)
def test_queries_that_can_write_require_approval(bash_contexts, command):
    from forgecli.security.permissions import has_permissions_to_use_tool

    bash, (parent, child) = bash_contexts
    assert not bash.is_read_only({"command": command})
    assert has_permissions_to_use_tool(bash, {"command": command}, parent).is_ask
    assert has_permissions_to_use_tool(bash, {"command": command}, child).is_deny


@pytest.mark.parametrize(
    "command",
    [
        "git branch",
        "git branch --list feature",
        "git branch --contains HEAD",
        "git tag --list v1",
        "git config --get user.name",
        "git config --list",
        "git stash list",
        "git log --oneline",
        "sed -n -e '1,60p' src/sample.txt",
    ],
)
def test_proven_queries_remain_allowed(bash_contexts, command):
    from forgecli.security.permissions import has_permissions_to_use_tool

    bash, contexts = bash_contexts
    assert bash.is_read_only({"command": command})
    for context in contexts:
        assert has_permissions_to_use_tool(bash, {"command": command}, context).is_allow


def test_cd_failures_and_semicolons_preserve_all_possible_directories(bash_contexts):
    from forgecli.security.shell.command_plan import ShellKind
    from forgecli.tools.builtin.Bash.bash_permissions import parse_for_permissions
    from forgecli.tools.builtin.Bash.directory_flow import directory_contexts

    _, (context, _) = bash_contexts
    command = "cd src && grep missing sample.txt; echo dirs; ls -la"
    contexts, error = directory_contexts(
        parse_for_permissions(command, ShellKind.POSIX, context.cwd), context
    )
    assert not error
    assert {c.cwd for c in contexts[1]} == {str(Path(context.cwd) / "src")}
    assert {c.cwd for c in contexts[-1]} == {
        context.cwd,
        str(Path(context.cwd) / "src"),
    }
    # || executes only on failure of cd; it must retain the original cwd.
    command = "cd src || ls -la"
    contexts, error = directory_contexts(
        parse_for_permissions(command, ShellKind.POSIX, context.cwd), context
    )
    assert not error
    assert {c.cwd for c in contexts[-1]} == {context.cwd}


@pytest.mark.parametrize(
    "command",
    [
        "cd src; cat ../outside.txt",
        "cd src || cat ../outside.txt",
        "cd src && grep absent sample.txt; cat ../outside.txt",
        "cd src && grep absent sample.txt | head -1; cat ../outside.txt",
    ],
)
def test_any_outside_branch_prevents_automatic_allow(bash_contexts, command):
    from forgecli.security.permissions import has_permissions_to_use_tool

    bash, contexts = bash_contexts
    for context in contexts:
        assert not has_permissions_to_use_tool(
            bash, {"command": command}, context
        ).is_allow


def test_read_path_rules_apply_to_bash(bash_contexts, tmp_path):
    from forgecli.core.permissions import PermissionRuleSource
    from forgecli.security.permissions import has_permissions_to_use_tool

    bash, contexts = bash_contexts
    (tmp_path / "escape").symlink_to(tmp_path.parent, target_is_directory=True)
    for context in contexts:
        for command in (
            "ls /etc",
            "cat escape/outside.txt",
            "grep needle escape/outside.txt",
            "sed -n '1,60p' escape/outside.txt",
            "cat < escape/outside.txt",
        ):
            assert not has_permissions_to_use_tool(
                bash, {"command": command}, context
            ).is_allow
        state = context.get_app_state()
        state = replace(
            state,
            tool_permission_context=replace(
                state.tool_permission_context,
                always_deny_rules={
                    PermissionRuleSource.SESSION: ("Read(./src/sample.txt)",)
                },
            ),
        )
        restricted = replace(context, get_app_state=lambda state=state: state)
        assert has_permissions_to_use_tool(
            bash, {"command": "cat src/sample.txt"}, restricted
        ).is_deny


def test_cd_reads_do_not_escape_via_changed_cwd(bash_contexts):
    from forgecli.security.permissions import has_permissions_to_use_tool

    bash, contexts = bash_contexts
    for context in contexts:
        # ../sample.txt is inside only after both &&-guarded directory changes.
        assert has_permissions_to_use_tool(
            bash, {"command": "cd src && cd nested && cat ../sample.txt"}, context
        ).is_allow
