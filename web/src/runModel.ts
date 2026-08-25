export type RunEvent = {
  event_id: string;
  kind: string;
  turn_id: string;
  sequence: number;
  occurred_at?: number;
  step_index?: number | null;
  request_id?: string | null;
  tool_call_id?: string | null;
  invocation_id?: string | null;
  payload: Record<string, unknown>;
};

/** 后端按 turn 归档的运行事件；流式增量已折叠成每次模型调用的一段正文。 */
export type RunSnapshot = {
  turn_id: string;
  events: RunEvent[];
  outputs: Record<string, string>;
};

export type LocalTurnStatus = "running" | "completed" | "failed" | "cancelled";

export type LocalTurn = {
  clientId: string;
  turnId: string | null;
  userText: string;
  /**
   * **已确认是最终回答**的那段正文。模型表态之前它一直是空的。
   *
   * 流式增量不进这里：一次以工具调用收尾的模型调用, 前面那段"我先看看文件"是过程而
   * 不是答案。先塞进回答区再收回去, 用户看到的就是一段话在聊天里闪一下又消失 ——
   * 而 outputs 里同一段文字这时又出现在处理过程里, 像是跳过去又跳回来。
   */
  assistantText: string;
  /** 被认定为最终回答的那次模型调用；在它出现之前, 正文只属于处理过程。 */
  answerRequestId: string | null;
  lastRequestId: string | null;
  /**
   * 结构性事件；**不含** `model_output_delta`。
   *
   * 一次长回答会推来上千条增量，把它们留在数组里，每条都要复制、排序、查重一遍，
   * 渲染开销就成了事件数的平方 —— 页面卡住就是这么来的。正文按调用存进 outputs。
   */
  events: RunEvent[];
  /** request_id -> 该次模型调用累积的可见正文。 */
  outputs: Record<string, string>;
  /** 本 turn 已处理到的事件序号；总线保证 turn 内单调递增，用它做 O(1) 去重。 */
  lastSequence: number;
  status: LocalTurnStatus;
  startedAt: number;
  error: string;
};

export type TurnMetrics = {
  elapsedMs: number;
  modelCalls: number;
  toolCalls: number;
  inputTokens: number;
  outputTokens: number;
  reasoningTokens: number;
  cachedTokens: number;
  totalTokens: number;
  estimated: boolean;
};

const terminalKinds = new Set(["turn_completed", "turn_failed", "turn_cancelled"]);

export function newLocalTurn(userText: string, now = Date.now()): LocalTurn {
  return {
    clientId: `local-${now}-${Math.random().toString(36).slice(2, 8)}`,
    turnId: null,
    userText,
    assistantText: "",
    answerRequestId: null,
    lastRequestId: null,
    events: [],
    outputs: {},
    lastSequence: 0,
    status: "running",
    startedAt: now,
    error: "",
  };
}

/** 同一次模型调用的分组键；模型事件与其正文必须落在同一个桶里。 */
export function modelKey(event: RunEvent) {
  return event.request_id ?? `call-${stringValue(event.payload.call_index) || "0"}`;
}

export function appendRunEvent(turns: LocalTurn[], event: RunEvent, now = Date.now()): LocalTurn[] {
  let source = turns;
  let index = source.findIndex((turn) => turn.turnId === event.turn_id);
  if (index < 0) {
    index = source.findIndex((turn) => turn.status === "running" && !turn.turnId);
  }
  if (index < 0) {
    // 刷新页面后重新接上仍在运行的 turn：补一个占位轮次，事件不再被丢弃。
    source = [...turns, { ...newLocalTurn("", now), turnId: event.turn_id }];
    index = source.length - 1;
  }

  const current = source[index];
  // 纯函数式去重：StrictMode 会拿同样的输入把 updater 跑两遍，不能靠副作用记账。
  if (event.sequence <= current.lastSequence) return turns;

  let assistantText = current.assistantText;
  let answerRequestId = current.answerRequestId;
  let lastRequestId = current.lastRequestId;
  let outputs = current.outputs;
  if (event.kind === "model_output_delta") {
    const delta = stringValue(event.payload.text);
    const requestId = modelKey(event);
    lastRequestId = requestId;
    outputs = { ...outputs, [requestId]: (outputs[requestId] ?? "") + delta };
    // 已经认定是最终回答的那次调用还在吐字, 正文跟着长。其余调用只进 outputs。
    if (answerRequestId === requestId) assistantText = outputs[requestId];
  }
  // 模型这次没再要工具, 它说的就是最终回答: 到这一刻才交给回答区, 而且只交一次。
  if (event.kind === "model_completed" && numberValue(event.payload.tool_call_count) === 0) {
    answerRequestId = event.request_id ?? lastRequestId;
    if (answerRequestId) assistantText = outputs[answerRequestId] ?? "";
  }

  let status = current.status;
  if (event.kind === "turn_completed") status = "completed";
  if (event.kind === "turn_failed") status = "failed";
  if (event.kind === "turn_cancelled") status = "cancelled";

  const next = [...source];
  next[index] = {
    ...current,
    turnId: current.turnId ?? event.turn_id,
    assistantText,
    answerRequestId,
    lastRequestId,
    outputs,
    events: event.kind === "model_output_delta" ? current.events : [...current.events, event],
    lastSequence: event.sequence,
    status,
  };
  return next;
}

/** 用后端的归档快照重建一个只读的处理过程。 */
export function restoreTurn(snapshot: RunSnapshot, now = Date.now()): LocalTurn {
  const events = [...(snapshot.events ?? [])].sort((a, b) => a.sequence - b.sequence);
  const terminal = [...events].reverse().find((event) => terminalKinds.has(event.kind));
  // 回答本身已经在 transcript 里了, 这里只要知道**哪次调用**是回答 —— 处理过程据此
  // 不再重复展示那一段, 否则刷新之后同一段文字会出现两遍。
  const answer = [...events].reverse().find(isAnswerCall);
  return {
    clientId: `restored-${snapshot.turn_id}`,
    turnId: snapshot.turn_id,
    userText: "",
    assistantText: "",
    answerRequestId: answer?.request_id ?? null,
    lastRequestId: null,
    events,
    outputs: snapshot.outputs ?? {},
    lastSequence: events.at(-1)?.sequence ?? 0,
    status: restoredStatus(terminal),
    startedAt: now,
    error: "",
  };
}

function isAnswerCall(event: RunEvent) {
  return event.kind === "model_completed" && numberValue(event.payload.tool_call_count) === 0;
}

function restoredStatus(terminal?: RunEvent): LocalTurnStatus {
  if (!terminal) return "running";
  if (terminal.kind === "turn_failed") return "failed";
  if (terminal.kind === "turn_cancelled") return "cancelled";
  return "completed";
}

export function finishLocalTurn(
  turns: LocalTurn[],
  turnId: string,
  responseText?: string,
  error = "",
): LocalTurn[] {
  return turns.map((turn) => {
    if (turn.turnId !== turnId) return turn;
    return {
      ...turn,
      assistantText: responseText?.trim() ? responseText : turn.assistantText,
      error: error || turn.error,
      status: error ? "failed" : turn.status,
    };
  });
}

export function failUnboundTurn(turns: LocalTurn[], clientId: string, error: string): LocalTurn[] {
  return turns.map((turn) => turn.clientId === clientId
    ? { ...turn, status: "failed", error }
    : turn);
}

export function isTerminalEvent(event: RunEvent) {
  return terminalKinds.has(event.kind);
}

export function shouldSendOnEnter(key: string, shiftKey: boolean, isComposing: boolean, compositionActive: boolean) {
  return key === "Enter" && !shiftKey && !isComposing && !compositionActive;
}

export function shouldAutoFollow(scrollHeight: number, scrollTop: number, clientHeight: number, threshold = 140) {
  return scrollHeight - scrollTop - clientHeight <= threshold;
}

export function metricsFor(events: RunEvent[], now = Date.now(), startedAt = now): TurnMetrics {
  const usage = events.filter((event) => event.kind === "model_usage");
  const finished = [...events].reverse().find((event) => terminalKinds.has(event.kind));
  const payload = finished?.payload ?? {};
  const totalFromCalls = usage.reduce((total, event) => {
    const explicit = numberValue(event.payload.total_tokens);
    return total + (explicit || numberValue(event.payload.input_tokens) + numberValue(event.payload.output_tokens));
  }, 0);
  return {
    elapsedMs: numberValue(payload.elapsed_ms) || Math.max(0, now - startedAt),
    modelCalls: numberValue(payload.model_calls) || events.filter((event) => event.kind === "model_started").length,
    toolCalls: numberValue(payload.tool_calls) || uniqueToolCount(events),
    inputTokens: sumPayload(usage, "input_tokens"),
    outputTokens: sumPayload(usage, "output_tokens"),
    reasoningTokens: sumPayload(usage, "reasoning_tokens"),
    cachedTokens: sumPayload(usage, "cached_tokens"),
    totalTokens: totalFromCalls,
    estimated: usage.some((event) => Boolean(event.payload.estimated)),
  };
}

export function groupToolEvents(events: RunEvent[]) {
  const groups: Array<{ id: string; events: RunEvent[] }> = [];
  for (const event of events) {
    if (!event.kind.startsWith("tool_") && !event.kind.startsWith("approval_") && event.kind !== "policy_resolved") continue;
    const key = event.invocation_id ?? event.tool_call_id ?? `tool-${event.payload.tool_name ?? event.sequence}`;
    let group = groups.find((item) => item.id === key);
    if (!group && event.invocation_id) {
      const name = stringValue(event.payload.tool_name);
      group = groups.find((item) => item.events.every((candidate) => !candidate.invocation_id) && stringValue(item.events[0]?.payload.tool_name) === name);
      if (group) group.id = event.invocation_id;
    }
    if (!group) {
      group = { id: key, events: [] };
      groups.push(group);
    }
    group.events.push(event);
  }
  return groups;
}

export function groupModelEvents(events: RunEvent[]) {
  const groups = new Map<string, RunEvent[]>();
  for (const event of events) {
    if (!event.kind.startsWith("model_")) continue;
    const key = modelKey(event);
    groups.set(key, [...(groups.get(key) ?? []), event]);
  }
  return [...groups.entries()].map(([id, items]) => ({ id, events: items }));
}

export function stringValue(value: unknown) {
  return value == null ? "" : String(value);
}

export function numberValue(value: unknown) {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function sumPayload(events: RunEvent[], key: string) {
  return events.reduce((total, event) => total + numberValue(event.payload[key]), 0);
}

function uniqueToolCount(events: RunEvent[]) {
  return new Set(events.filter((event) => event.kind === "tool_queued").map((event) => (
    event.invocation_id ?? event.tool_call_id ?? event.event_id
  ))).size;
}

// ---- 处理过程的步骤时间线 ----

export type ToolGroup = { id: string; events: RunEvent[] };

export type ToolCategory = { id: string; label: string };

export type TimelineItem =
  | { id: string; kind: "model"; key: string; sequence: number; events: RunEvent[]; reason: string }
  | { id: string; kind: "tools"; sequence: number; category: ToolCategory; groups: ToolGroup[]; reason: string }
  | { id: string; kind: "note"; sequence: number; event: RunEvent; reason: string };

const NOTE_KINDS = ["todo_updated", "plan_proposed"];

const OTHER_TOOLS: ToolCategory = { id: "other", label: "其他工具" };

/**
 * 按"做了哪一类事"归并, 判据是**这次调用声明的能力**而不是工具名。
 *
 * 名字清单会悄悄过期: `fs.search_text` 在这份清单里躺了很久, 而真名是 `search_text`,
 * 于是每一次搜索都被归进"其他工具", 没有任何东西会因此报错。能力是后端裁决用的同一
 * 组事实, 新增工具自动落到对的那一类。
 */
const CAPABILITY_CATEGORIES: Array<{ id: string; label: string; capabilities: string[] }> = [
  { id: "shell", label: "执行命令", capabilities: ["execute_shell", "execute_script"] },
  { id: "write", label: "修改文件", capabilities: ["workspace_write", "workspace_delete", "path_move", "external_write"] },
  { id: "read", label: "读取文件", capabilities: ["workspace_read", "external_read"] },
  { id: "planning", label: "计划与待办", capabilities: ["plan_only"] },
];

/**
 * 连 prepare 都没走到的调用没有能力可看, 只能按名字认 —— 而且只认**确切认识**的那些。
 *
 * 不按命名空间猜: 模型编出来的 `fs_write_file` 也以 `fs_` 开头, 猜成"读取文件"就是在
 * 替一次没发生过的写入洗白。归到"其他工具"才是实话 —— 那次调用是什么, 我们确实不知道。
 *
 * 名字随 ADR-0036 从点号改成下划线; 清单同时清掉了 ADR-0029 就删掉的 fs.create_file /
 * fs.list_files 之类 —— 正是上面那段注释警告过的"悄悄过期"。
 */
const NAME_CATEGORIES: Array<{ id: string; label: string; match: (name: string) => boolean }> = [
  { id: "shell", label: "执行命令", match: (name) => name.startsWith("shell_") },
  { id: "write", label: "修改文件", match: (name) => ["fs_apply_patch"].includes(name) },
  { id: "read", label: "读取文件", match: (name) => ["fs_read", "fs_find", "search_text", "git_read", "artifact_read"].includes(name) },
  { id: "planning", label: "计划与待办", match: (name) => name.startsWith("plan_") || name.startsWith("todo_") },
];

export function categoryOf(toolName: string, capabilities: string[] = []): ToolCategory {
  // 顺序即优先级: 一次调用同时声明读和写时, 它是一次写入。
  const byCapability = CAPABILITY_CATEGORIES.find((item) => item.capabilities.some((name) => capabilities.includes(name)));
  if (byCapability) return { id: byCapability.id, label: byCapability.label };
  const byName = NAME_CATEGORIES.find((item) => item.match(toolName));
  return byName ? { id: byName.id, label: byName.label } : OTHER_TOOLS;
}

/** 这次调用声明的能力, 取自 prepare 之后的那条事件。 */
export function capabilitiesOf(events: RunEvent[]): string[] {
  const prepared = events.find((event) => event.kind === "tool_prepared");
  const raw = prepared?.payload.capabilities;
  return Array.isArray(raw) ? raw.map(stringValue) : [];
}

export function toolNameOf(events: RunEvent[]) {
  const named = events.find((event) => event.payload.tool_name);
  return stringValue(named?.payload.tool_name) || "工具";
}

/**
 * 把一轮的事件排成给人看的步骤。
 *
 * 顺序不能反: 先按 sequence 排成一条真实时间线, **再**合并相邻同类。反过来做 —— 在
 * 只含工具的列表里判"相邻" —— 会把中间隔着一次模型调用的两次读文件也当成连续的, 于是
 * 合并出来的那一段带着更早的 sequence, 排到模型调用前面去, 时间线就说了假话。
 */
export function buildTimeline(events: RunEvent[]): TimelineItem[] {
  const items: TimelineItem[] = groupModelEvents(events).map((group) => ({
    id: `model-${group.id}`,
    kind: "model",
    key: group.id,
    events: group.events,
    sequence: Math.min(...group.events.map((event) => event.sequence)),
    reason: "",
  }));
  for (const group of groupToolEvents(events)) {
    items.push({
      id: `tools-${group.id}`,
      kind: "tools",
      sequence: Math.min(...group.events.map((event) => event.sequence)),
      category: categoryOf(toolNameOf(group.events), capabilitiesOf(group.events)),
      groups: [group],
      reason: "",
    });
  }
  for (const event of events) {
    if (!NOTE_KINDS.includes(event.kind)) continue;
    items.push({ id: `note-${event.event_id}`, kind: "note", event, sequence: event.sequence, reason: "" });
  }
  items.sort((a, b) => a.sequence - b.sequence);
  return merge(attachReasons(items, events));
}

/**
 * 决策摘要不单独占一行，挂到它解释的那一步上。
 *
 * 每次派发工具循环都会发一条 DECISION_SUMMARY，摊平之后时间线就是"工具 / 决策 /
 * 工具 / 决策"交替，一半的行在重复上一行已经写明的事。更要命的是它们夹在工具之间，
 * 连着六次读文件因此没有一次是"相邻"的，聚合根本合不起来。
 *
 * 归属看的是**下一步开始之前**：循环先发 TOOL_QUEUED 再发决策，所以一条决策说的是
 * 它上面那一步，不是下面那一步。
 */
function attachReasons(items: TimelineItem[], events: RunEvent[]): TimelineItem[] {
  const decisions = events.filter((event) => event.kind === "decision_summary");
  if (!decisions.length) return items;
  let cursor = 0;
  return items.map((item, index) => {
    const next = items[index + 1]?.sequence ?? Number.POSITIVE_INFINITY;
    let reason = "";
    while (cursor < decisions.length && decisions[cursor].sequence < next) {
      reason = reason || stringValue(decisions[cursor].payload.reason_summary);
      cursor += 1;
    }
    return reason ? { ...item, reason } : item;
  });
}

/** 相邻的同类调用并成一段: 连着读六个文件是一件事, 摊成六行只会淹掉真正要看的东西。 */
function merge(items: TimelineItem[]): TimelineItem[] {
  const merged: TimelineItem[] = [];
  for (const item of items) {
    const previous = merged.at(-1);
    if (item.kind === "tools" && previous?.kind === "tools" && previous.category.id === item.category.id) {
      previous.groups.push(...item.groups);
      continue;
    }
    merged.push(item.kind === "tools" ? { ...item, groups: [...item.groups] } : item);
  }
  return merged;
}

/** 这次调用有没有真的执行过 —— 与"执行了然后失败了"是两回事。 */
export function wasExecuted(completed: RunEvent | undefined) {
  return !completed || completed.payload.executed !== false;
}

/** 这次模型调用的正文该不该出现在处理过程里。 */
export function showsOutput(completed: RunEvent | undefined) {
  // 还没收尾: 正在流的正文只有处理过程能看到。
  // 收尾且没要工具: 那段文字就是最终回答, 它在回答区, 这里不再重复。
  return !completed || numberValue(completed.payload.tool_call_count) > 0;
}
