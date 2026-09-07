export type RunEvent = {
  event_id: string;
  kind: string;
  /** 这条事件属于哪个会话。turn_id 在每个会话里各自从 1 起, 单独用它定位不了一轮 (ADR-0048 决策 2)。 */
  session_id: string;
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
  session_id?: string;
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
  // 合计里属于上下文压缩的那部分。压缩是 Forge 自己发起的额外模型调用, 混在总数里
  // 不标出来, 用户拿总数去核账单时会发现一次对不上的调用 (ADR-0037)。
  compactTokens: number;
  estimated: boolean;
};

/**
 * token 数的显示口径: 不足 1000 给整数, 到了 1000 换成 k。
 *
 * 一位小数就够: 这个数字是拿来判断量级的 (这轮烧得多不多), 不是拿来对账的 ——
 * 要对账得看供应商账单, 而那里的口径本来就与本地估算不同。
 * 尾随的 .0 去掉: `1.0k` 看起来像是精确到百位, 其实不是。
 */
export function formatTokens(value: number) {
  if (value < 1000) return String(Math.round(value));
  return `${(value / 1000).toFixed(1).replace(/\.0$/, "")}k`;
}

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

/**
 * 一轮失败时给人的安全错误摘要。
 *
 * 后端已经把供应商响应归一化、截断后放进 `turn_failed.detail`；这里不再猜异常类型，也不
 * 回退到原始响应体。取最后一条是为了兼容事件重放以及未来可能增加的阶段性失败事件。
 */
export function terminalFailureDetail(events: RunEvent[]) {
  const failed = [...events].reverse().find((event) => event.kind === "turn_failed");
  return stringValue(failed?.payload.detail);
}

export function shouldSendOnEnter(key: string, shiftKey: boolean, isComposing: boolean, compositionActive: boolean) {
  return key === "Enter" && !shiftKey && !isComposing && !compositionActive;
}

export function shouldAutoFollow(scrollHeight: number, scrollTop: number, clientHeight: number, threshold = 140) {
  return scrollHeight - scrollTop - clientHeight <= threshold;
}

export function metricsFor(events: RunEvent[], now = Date.now(), startedAt = now): TurnMetrics {
  const usage = events.filter((event) => event.kind === "model_usage");
  const compaction = usage.filter((event) => isCompactionUsage(event));
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
    compactTokens: compaction.reduce((total, event) => {
      const explicit = numberValue(event.payload.total_tokens);
      return total + (explicit || numberValue(event.payload.input_tokens) + numberValue(event.payload.output_tokens));
    }, 0),
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
      group = groups.find((item) => (
        item.events.every((candidate) => !candidate.invocation_id)
        && !item.events.some((candidate) => candidate.kind === "tool_completed" || candidate.kind === "tool_cancelled")
        && stringValue(item.events[0]?.payload.tool_name) === name
      ));
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

/** 这条用量属于上下文压缩, 不属于 Agent 自己的某次模型调用。 */
export function isCompactionUsage(event: RunEvent) {
  return event.kind === "model_usage" && stringValue(event.payload.origin) === "compact";
}

export function groupModelEvents(events: RunEvent[]) {
  const groups = new Map<string, RunEvent[]>();
  for (const event of events) {
    if (!event.kind.startsWith("model_")) continue;
    // 压缩的用量事件带着自己的 request_id, 不挡掉的话会在时间线上多出一个只有用量、
    // 没有开始也没有结束的"模型调用", 而且永远显示成运行中。它属于压缩那一行。
    if (isCompactionUsage(event)) continue;
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

/**
 * 工具活动收成的那一行。
 *
 * `mono` 表示它是一条命令, 该用等宽显示。`count` 交给渲染层单独摆一列, 不拼进 text ——
 * 拼进去就要在标题里做"读取文件"→"读取 6 个文件"这种动宾拆分, 而那对
 * "执行 Shell 命令""按符号名找定义"根本拆不开。
 */
export type ToolSummary = { text: string; mono: boolean; count: number };


/**
 * 把一组工具活动收成一行。
 *
 * 写的是模型已经发出的**结构化调用请求**，不是自然语言里一句“准备干嘛”。请求可能仍在
 * 排队或随后被安全策略拒绝；真实状态由同一组里的后续事件决定。
 *
 * 名字一律取后端目录里的 title (`toolActionLabel`), 不在前端另写一套动词表。
 *
 * **多次调用只报次数, 不摆目标**: 摆第一个再跟一句"等 N 项", 等于让一个随机挑出来的
 * 文件占满整行, 而它并不比另外几个更值得看。要看是哪几个就展开。
 */
export function summariseTools(groups: ToolGroup[], directory: ToolDirectory = {}): ToolSummary {
  const names = [...new Set(groups.map((group) => toolNameOf(group.events)))];
  const labels = names.map((name) => toolActionLabel(name, directory));
  // 目录里没有这个名字时 toolActionLabel 退回工具名本身 —— 那不是一个可信的说法。
  const known = labels.every((label, index) => label !== names[index]);
  const count = groups.length;
  if (!known) return { text: "工具调用", mono: false, count };

  // 一组里混了好几种工具 (同属一个动作才会被并到一起): 把种类列出来。
  if (names.length > 1) return { text: labels.join(" / "), mono: false, count };

  const label = labels[0];
  // shell_run 的命令详情放在展开后的调用行里；组级摘要只说明动作，避免把整条命令
  // （或一条很长的管道）塞进折叠标题。这里使用稳定的界面文案，不暴露后端 title 的
  // 实现细节（当前 title 是“执行 Shell 命令”）。
  if (names[0] === "shell_run") return { text: "执行命令", mono: false, count };
  // 其余一律只给动作名: 具体读了哪个文件展开才看得到。收起时摆一个路径, 等于让一个
  // 随机挑出来的文件占满整行, 而它并不比另外几个更值得看。
  return { text: label, mono: false, count };
}

export type TimelineItem =
  | { id: string; kind: "model"; key: string; sequence: number; events: RunEvent[]; reason: string }
  | { id: string; kind: "tools"; sequence: number; category: ToolCategory; mergeKey: string; groups: ToolGroup[]; reason: string }
  | { id: string; kind: "note"; sequence: number; event: RunEvent; reason: string };

const NOTE_KINDS = ["todo_updated", "plan_proposed", "context_compacted"];

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
  { id: "read", label: "读取文件", capabilities: ["workspace_read", "external_read", "artifact_read"] },
  { id: "planning", label: "任务", capabilities: ["plan_only"] },
  { id: "memory", label: "记忆", capabilities: ["memory_write"] },
];

/**
 * 后端工具目录里的一条, 取自 `GET /api/v1/tools` 的 `all`。
 *
 * `title` 与 `capabilities` 都是 `ToolSpec` 上的字段, 前端不另存一份。这里曾经有一张手抄
 * 的名字表, 它按定义会过期: 后端把 `artifact_read` 的 title 改成"读回已归档的输出"之后,
 * 界面上仍然写着"读取产物", 而没有任何东西会因此报错。
 *
 * `capabilities` 原先是 `action` —— 后端一个只服务于展示的分类字段。它已随 ADR-0042
 * 删除, 归类改走能力: 那样这里只剩 CAPABILITY_CATEGORIES 一张表, 而不是两张需要互相
 * 对齐的表。
 */
export type ToolDirectoryEntry = { title: string; capabilities: string[] };
export type ToolDirectory = Record<string, ToolDirectoryEntry>;

/**
 * 步骤标题上那一行: 认得的用它自己的 title, 认不得的老老实实显示工具名。
 *
 * 类别 ("任务" / "读取文件") 回答的是"哪一类事", 这个回答"具体哪件事" —— 四个任务工具
 * 全落在同一类里, 而"更新待办状态"和"重写待办"对看的人不是一回事。
 *
 * 目录里没有的名字退回工具名本身: 编一个动作名出来会让一次没发生过的操作看起来发生过。
 * 目录还没拉回来时整张表是空的, 那时每一行都显示工具名, 不显示错的中文。
 */
export function toolActionLabel(toolName: string, directory: ToolDirectory = {}) {
  return directory[toolName]?.title || toolName;
}

export function categoryOf(toolName: string, capabilities: string[] = [], directory: ToolDirectory = {}): ToolCategory {
  // 顺序即优先级: 一次调用同时声明读和写时, 它是一次写入。
  const byCapability = CAPABILITY_CATEGORIES.find((item) => item.capabilities.some((name) => capabilities.includes(name)));
  if (byCapability) return { id: byCapability.id, label: byCapability.label };
  // 连 prepare 都没走到的调用没有能力可看, 这时退回目录里那个工具**声明**的能力上界。
  // 不按命名空间猜: 模型编出来的 `fs_write_file` 也以 `fs_` 开头, 猜成"读取文件"就是在
  // 替一次没发生过的写入洗白 —— 目录里没有的名字归"其他工具"。
  const declared = directory[toolName]?.capabilities ?? [];
  const byDeclared = CAPABILITY_CATEGORIES.find((item) => item.capabilities.some((name) => declared.includes(name)));
  return byDeclared ? { id: byDeclared.id, label: byDeclared.label } : OTHER_TOOLS;
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
/**
 * 折叠状态下那一行"现在在干什么"。
 *
 * 处理过程默认是收起的, 所以这一行是运行期间用户唯一看得到的进度 —— 它必须说得具体,
 * "处理中"三个字等于没说。取的是**最后一件还没收尾的事**, 不是最后一条事件: 工具跑完
 * 之后紧跟着的那条 model_started 才是当下在发生的。
 */
export function activityOf(turn: LocalTurn, directory: ToolDirectory = {}): string {
  if (turn.status === "completed") return "处理完成";
  if (turn.status === "cancelled") return "处理已取消";
  if (turn.status === "failed") return "处理失败";

  let modelRunning = false;
  let thinking = false;
  let awaiting = false;
  let lastNote = "";

  for (const event of turn.events) {
    switch (event.kind) {
      case "approval_requested":
        awaiting = true;
        break;
      case "approval_resolved":
        awaiting = false;
        break;
      case "model_started":
        modelRunning = true;
        thinking = false;
        break;
      case "model_completed":
      case "model_failed":
        modelRunning = false;
        thinking = false;
        break;
      case "model_reasoning_status":
        thinking = stringValue(event.payload.status) === "started";
        break;
      case "todo_updated":
        lastNote = "更新任务";
        break;
      case "plan_proposed":
        lastNote = "提交计划";
        break;
      case "context_compacted":
        lastNote = "压缩上下文";
        break;
      default:
        break;
    }
  }

  // 等审批排在最前: 这时候什么都没在跑, 而用户正是那个卡住流程的人。
  if (awaiting) return "等待你的审批";
  // 通过分组后的生命周期判断，避免 queued 用 tool_call_id、started/completed 用
  // invocation_id 时同一次调用在 Map 里留下两个身份。队列是串行的，不能称作“并行处理”。
  const activeTools = groupToolEvents(turn.events).filter((group) => !group.events.some(
    (event) => event.kind === "tool_completed" || event.kind === "tool_cancelled",
  ));
  const runningTools = activeTools.filter((group) => group.events.some((event) => event.kind === "tool_started"));
  if (runningTools.length > 0) {
    const names = [...new Set(runningTools.map((group) => toolNameOf(group.events)).filter(Boolean))];
    const label = names.length === 1 ? toolActionLabel(names[0], directory) : `${runningTools.length} 个工具`;
    const waiting = activeTools.length - runningTools.length;
    return `正在${label}${waiting > 0 ? `，另有 ${waiting} 个待处理` : ""}`;
  }
  if (activeTools.length > 0) {
    const names = [...new Set(activeTools.map((group) => toolNameOf(group.events)).filter(Boolean))];
    if (activeTools.length === 1 && names.length === 1) return `准备调用${toolActionLabel(names[0], directory)}`;
    return `准备调用 ${activeTools.length} 个工具`;
  }
  if (modelRunning) return thinking ? "模型思考中…" : "模型生成中…";
  if (lastNote) return lastNote;
  return turn.events.length ? "正在处理" : "正在建立本轮事件流…";
}

/**
 * 这一次模型调用有没有话可显示。
 *
 * 判据必须用**渲染器用的那份数据** (outputs), 不能用 model_completed 里的 text_chars:
 * 实测模型在每次工具调用旁边会吐一个空白字符, 于是 text_chars=1 —— 计数说"它说话了",
 * 而屏幕上什么都没有。那个看不见的节点横在中间, 让相邻的工具活动永远合并不了。
 *
 * 规则是"渲染不出东西就不进时间线", 所以判据和渲染必须看同一份东西。
 */
function speaks(events: RunEvent[], text: string): boolean {
  if (events.some((event) => event.kind === "model_failed")) return true;
  const completed = events.find((event) => event.kind === "model_completed");
  // 还没收尾: 留着, 那是"思考中…/生成中…"的位置。
  return !completed || text.trim().length > 0;
}

export function buildTimeline(
  events: RunEvent[],
  directory: ToolDirectory = {},
  outputs: Record<string, string> = {},
): TimelineItem[] {
  const items: TimelineItem[] = groupModelEvents(events)
    // 一个字都没吐、只发了工具调用的模型节点不进时间线。
    //
    // 它渲染不出任何东西, 却横在两组工具活动中间, 让相邻合并永远不成立 —— 实测一屏
    // 18 次工具调用一次都没并起来, 就是因为每次调用前面都隔着一个看不见的模型节点。
    // "模型跑了几次"由底部的指标行回答, 不需要在流里占位。
    .filter((group) => speaks(group.events, outputs[group.id] ?? ""))
    .map((group) => ({
      id: `model-${group.id}`,
      kind: "model",
      key: group.id,
      events: group.events,
      sequence: Math.min(...group.events.map((event) => event.sequence)),
      reason: "",
    }));
  for (const group of groupToolEvents(events)) {
    const name = toolNameOf(group.events);
    items.push({
      id: `tools-${group.id}`,
      kind: "tools",
      sequence: Math.min(...group.events.map((event) => event.sequence)),
      category: categoryOf(name, capabilitiesOf(group.events), directory),
      // 合并粒度用**工具名**而不是类别: 按类别并会把 6 次 fs_find 与 10 次 fs_read 压成
      // 一行"18 次工具调用" —— 从一个极端跳到另一个。这里原先用的是后端的 ToolAction,
      // 但那个字段已随 ADR-0042 删除, 而工具名比它更细也更准: `fs_read` 与 `git_read`
      // 曾经同属 "read", 并成一行其实读不出发生了什么。
      // 认不出名字的调用退回类别, 不让它们各自成行。
      mergeKey: name || categoryOf(name, capabilitiesOf(group.events), directory).id,
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
    if (item.kind === "tools" && previous?.kind === "tools" && previous.mergeKey === item.mergeKey) {
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
