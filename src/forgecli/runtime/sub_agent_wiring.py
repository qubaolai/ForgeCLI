"""将子资源绑定到调度会话和所选模型上."""

from pathlib import Path

from forgecli.agent.loop.builtin_loop import BuiltinAgentLoop
from forgecli.agent.loop.rules import builtin_rules
from forgecli.agent.run_bus import AgentRunEventBus
from forgecli.agent.sub_agent.runner import SubAgentResources
from forgecli.agent.tool_observer import EventBusToolRunObserver
from forgecli.common.cancellation import CancelToken
from forgecli.conversation.window_manager import WindowManager
from forgecli.core.agent.state import AssembledContext, ContextFingerprints
from forgecli.core.agent.sub_agent import SubAgentIdentity
from forgecli.core.context.budget import ContextBudget
from forgecli.core.model.origin import RequestOrigin
from forgecli.core.model.usage import UsageRecordDraft
from forgecli.core.tool.hashing import digest, digest_text
from forgecli.llm.transport_policy import ModelTransportPolicy
from forgecli.prompt import prompts
from forgecli.prompt.project_instruction_reader import FsProjectInstructionReader
from forgecli.prompt.session_facts import SessionFacts
from forgecli.runtime.llm_wiring import LlmRuntime
from forgecli.session.service import SessionService
from forgecli.tools.runner import ToolRunner, sub_agent_runner


def prepare_sub_agent(
    identity: SubAgentIdentity,
    cancel: CancelToken,
    model_calls: int,
    *,
    llm: LlmRuntime,
    parent: ToolRunner,
    session: SessionService,
    bus: AgentRunEventBus,
    facts: SessionFacts,
) -> SubAgentResources:
    if session.current().session_id != identity.session_id:
        raise ValueError("Child dispatch belongs to another session")
    model = llm.resolve_model(origin=RequestOrigin.SUB_AGENT)
    compact_model = llm.resolve_model(origin=RequestOrigin.COMPACT)
    entry = model.entry
    budget = ContextBudget(
        entry.context_window,
        entry.max_output_tokens or 0,
        entry.effective_context_tokens,
    )
    window = WindowManager(
        max_inline_bytes=50000,
        gateway=llm.gateway,
        meter=llm.usage_meter,
        compact_model=compact_model,
    )
    loop = BuiltinAgentLoop(
        llm.gateway,
        llm.usage_meter,
        rules=builtin_rules(context=window, max_model_calls=model_calls),
        cancel_token_factory=lambda: cancel,
        resolved_model=model,
        event_bus=bus,
        model_transport_policy=ModelTransportPolicy(),
    )
    tools = sub_agent_runner(parent, identity, EventBusToolRunObserver(bus))
    text = prompts.sub_agent_system_prompt(
        facts, FsProjectInstructionReader().read(parent.workspace_roots)
    )
    schemas = tools.tool_schemas()
    context = AssembledContext(
        text,
        ContextFingerprints(digest_text(text), digest(facts), digest(schemas)),
        tools=schemas,
        budget=budget,
    )
    path = (
        Path(parent.tool_results_dir()).parent / "agents" / f"{identity.agent_id}.json"
    )

    def record(draft: UsageRecordDraft) -> None:
        if draft.session_id != identity.session_id or draft.turn_id != identity.turn_id:
            raise ValueError("Child usage identity mismatch")
        session.record_usage(draft.to_payload(), turn_id=identity.turn_id)

    return SubAgentResources(loop, tools, context, path, record)
