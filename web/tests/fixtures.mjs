/** 渲染快照用的固定数据。
 *
 * 只有这一份, 重构前后共用。它不描述某个真实会话, 而是把每个分支都摆一遍:
 * 有工具的模型调用与没工具的、成功与失败、有脚本的审批与只读的审批。
 */

export const directory = {
  fs_read: { title: "读取文件", capabilities: ["workspace_read"] },
  fs_write: { title: "写入文件", capabilities: ["workspace_write"] },
  shell_run: { title: "执行 Shell 命令", capabilities: ["execute_shell"] },
};

function event(kind, sequence, payload = {}, extra = {}) {
  return {
    event_id: `e${sequence}`,
    kind,
    session_id: "sess-1",
    turn_id: "turn-1",
    sequence,
    payload,
    ...extra,
  };
}

function toolCall(base, name, invocation, options = {}) {
  const target = options.target ?? "README.md";
  return [
    event(
      "tool_queued",
      base,
      { tool_name: name, queue_position: 0, arguments: [["path", target]] },
      { tool_call_id: invocation },
    ),
    event(
      "tool_prepared",
      base + 1,
      {
        tool_name: name,
        capabilities: directory[name].capabilities,
        targets: [target],
        target_count: 1,
        arguments: [["path", target]],
      },
      { invocation_id: invocation, tool_call_id: invocation },
    ),
    event(
      "policy_resolved",
      base + 2,
      {
        decision: "allow",
        reason: "只读命令",
        matched_rule_id: "rule-7",
        risk_facts: [],
        detail: "",
      },
      { invocation_id: invocation },
    ),
    event("tool_started", base + 3, { tool_name: name }, { invocation_id: invocation }),
    event(
      "tool_completed",
      base + 4,
      {
        tool_name: name,
        status: options.status ?? "ok",
        elapsed_ms: options.elapsedMs ?? 42,
        result_summary: options.summary ?? "读到 120 行",
        error_summary: options.error ?? "",
        executed: true,
        bytes_out: 3400,
      },
      { invocation_id: invocation },
    ),
  ];
}

/** 跑完的一轮: 两次模型调用, 两组工具, 三条 note, 一次压缩用量。 */
export const completedEvents = [
  event("model_started", 1, {}, { request_id: "req-1" }),
  event("model_completed", 2, { tool_call_count: 2 }, { request_id: "req-1" }),
  ...toolCall(3, "fs_read", "inv-1"),
  ...toolCall(8, "fs_read", "inv-2", { target: "package.json" }),
  event("decision_summary", 13, { reason_summary: "先看这两个文件再决定改哪里" }),
  event("todo_updated", 14, { current: "拆 App.tsx", done: 1, total: 3 }),
  event("plan_proposed", 15, { title: "前端重构", revision: 2, step_count: 8 }),
  event("context_compacted", 16, { tokens_saved: 12000, messages_replaced: 9 }),
  event(
    "model_usage",
    17,
    {
      input_tokens: 5000,
      output_tokens: 400,
      reasoning_tokens: 120,
      cached_tokens: 3000,
      total_tokens: 5400,
    },
    { request_id: "req-1" },
  ),
  event(
    "model_usage",
    18,
    { origin: "compact", input_tokens: 800, output_tokens: 100, total_tokens: 900 },
    { request_id: "req-c" },
  ),
  event("model_started", 19, {}, { request_id: "req-2" }),
  event("model_completed", 20, { tool_call_count: 0 }, { request_id: "req-2" }),
  event("turn_completed", 21, { elapsed_ms: 8400, model_calls: 2, tool_calls: 2 }),
];

export const completedOutputs = {
  "req-1": "我先看看这两个文件。",
  "req-2": "## 结论\n\n改动集中在 `App.tsx`，一共三处：\n\n1. 删掉死函数\n2. 拆出布局\n3. 收敛 prop\n",
};

/** 还在跑的一轮: 工具排队中, 模型思考中, 没有终态事件。 */
export const runningEvents = [
  event("model_started", 1, {}, { request_id: "req-1" }),
  event("model_reasoning_status", 2, { status: "started" }, { request_id: "req-1" }),
  event("model_completed", 3, { tool_call_count: 1 }, { request_id: "req-1" }),
  event(
    "tool_queued",
    4,
    { tool_name: "shell_run", queue_position: 0, arguments: [["command", "npm run build"]] },
    { tool_call_id: "inv-9" },
  ),
  event(
    "tool_prepared",
    5,
    {
      tool_name: "shell_run",
      capabilities: ["execute_shell"],
      targets: [],
      target_count: 0,
      arguments: [["command", "npm run build"]],
    },
    { invocation_id: "inv-9", tool_call_id: "inv-9" },
  ),
  event("approval_requested", 6, {}, { invocation_id: "inv-9" }),
];

/** 失败的一轮: 模型层报错 + 终态详情。 */
export const failedEvents = [
  event("model_started", 1, {}, { request_id: "req-1" }),
  event(
    "model_failed",
    2,
    { message: "供应商返回 429", error_kind: "rate_limited" },
    { request_id: "req-1" },
  ),
  event("turn_failed", 3, {
    detail: "供应商返回 429，已重试 3 次",
    elapsed_ms: 2100,
    model_calls: 1,
    tool_calls: 0,
  }),
];

const startedAt = 1730000000000;

export function turnOf(events, outputs, status, extra = {}) {
  return {
    clientId: "local-fixture",
    turnId: "turn-1",
    userText: "把前端重构一下",
    assistantText: outputs["req-2"] ?? "",
    answerRequestId: outputs["req-2"] ? "req-2" : null,
    lastRequestId: null,
    events,
    outputs,
    lastSequence: events.at(-1).sequence,
    status,
    startedAt,
    error: "",
    ...extra,
  };
}

export const completedTurn = turnOf(completedEvents, completedOutputs, "completed");
export const runningTurn = turnOf(runningEvents, { "req-1": "我来跑一次构建。" }, "running");
export const failedTurn = turnOf(failedEvents, {}, "failed", { assistantText: "", answerRequestId: null });

export const transcriptItem = {
  event_id: "tr-2",
  type: "message",
  created_at: "2026-09-08T10:00:00",
  payload: { role: "assistant", text: "改动集中在 `App.tsx`。", turn_id: "turn-1" },
};

export const userItem = {
  event_id: "tr-1",
  type: "message",
  created_at: "2026-09-08T09:59:00",
  payload: { role: "user", text: "把前端重构一下" },
};

/** 高危审批: 有删除, 有脚本正文, 有写入预览。 */
export const criticalApproval = {
  approval_id: "ap-1",
  mandatory: false,
  view: {
    mode: "workspace_write",
    tool_name: "shell_run",
    workspace_roots: ["/Users/dev/repo"],
    raw_command: "rm -rf build && npm ci",
    target_resolution: "resolved",
    target_groups: [
      { label: "删除", paths: ["build/"] },
      { label: "写入", paths: ["node_modules/"] },
      { label: "读取", paths: ["package.json"] },
    ],
    script_snapshots: [
      { language: "bash", origin: "inline", path: "", source: "set -e\nrm -rf build\nnpm ci" },
    ],
    content_previews: [{ path: "src/a.ts", content: "export const a = 1;", truncated: true }],
    counts: [
      { label: "删除", count: 1 },
      { label: "写入", count: 1 },
      { label: "读取", count: 1 },
      { label: "网络", count: 0 },
      { label: "外部副作用", count: 0 },
    ],
    unresolved_reason: null,
    allowed_scopes: ["once", "workspace"],
    learn_blocked_reason: "",
  },
};

/** 只读审批: 不能记成规则, 没有证据分块。 */
export const calmApproval = {
  approval_id: "ap-2",
  mandatory: false,
  view: {
    mode: "read_only",
    tool_name: "shell_run",
    workspace_roots: ["/Users/dev/repo"],
    raw_command: "git status",
    target_resolution: "resolved",
    target_groups: [{ label: "读取", paths: [".git/HEAD"] }],
    script_snapshots: [],
    content_previews: [],
    counts: [
      { label: "删除", count: 0 },
      { label: "写入", count: 0 },
      { label: "读取", count: 1 },
      { label: "网络", count: 0 },
      { label: "外部副作用", count: 0 },
    ],
    unresolved_reason: null,
    allowed_scopes: ["once"],
    learn_blocked_reason: "命令里有变量展开，参数结构不固定",
  },
};

export const settings = [
  {
    key: "output.theme",
    label: "界面主题",
    help: "深色或浅色",
    level: "app",
    kind: "choice",
    value: "dark",
    choices: ["dark", "light"],
    default: "dark",
    overridden: false,
    effect: "立即生效",
  },
  {
    key: "run.stream",
    label: "流式输出",
    help: "边生成边显示",
    level: "project",
    kind: "bool",
    value: "true",
    choices: [],
    default: "false",
    overridden: true,
    effect: "下一轮生效",
  },
  {
    key: "run.timeout",
    label: "单轮超时",
    help: "",
    level: "project",
    kind: "text",
    value: "600",
    choices: [],
    default: "300",
    overridden: true,
    effect: "下一轮生效",
  },
];

export const providers = [
  {
    id: "deepseek",
    name: "DeepSeek",
    api_base: "https://api.deepseek.com/v1/chat/completions",
    api_key_env: "DEEPSEEK_API_KEY",
    timeout: 60,
    max_retries: 2,
    models: [
      {
        provider: "deepseek",
        id: "deepseek-chat",
        params: { context_window: 65536, temperature: 0.7, thinking_mode: "off" },
      },
    ],
  },
];

export const adminFixture = {
  roots: [
    { path: "/Users/dev/repo", access: "write" },
    { path: "/Users/dev/notes", access: "read" },
  ],
  rules: [
    { rule_id: "r-1", label: "允许 npm ci", scope: "workspace", revoked: false, match: { mode: "prefix" } },
  ],
  checkpoints: [
    {
      checkpoint_id: "cp-1",
      status: "committed",
      snapshot_strategy: "copy",
      created_at: "2026-09-08T09:00:00",
      mutations: { entries: [] },
    },
  ],
  providers,
  providerSettings: providers,
  providerFields: [
    { name: "api_base", label: "API 地址", kind: "text" },
    { name: "timeout", label: "超时（秒）", kind: "number" },
  ],
  modelFields: [
    { name: "context_window", label: "上下文窗口", kind: "number" },
    { name: "temperature", label: "温度", kind: "number" },
  ],
  knownProviders: [
    { id: "deepseek", label: "DeepSeek", api_key_env: "DEEPSEEK_API_KEY", available: true, builtin: true },
    { id: "acme", label: "Acme", api_key_env: "ACME_API_KEY", available: false, builtin: false },
  ],
  providerProtocols: [
    { value: "openai_compatible", label: "OpenAI Compatible", supported: true },
    { value: "anthropic", label: "Anthropic", supported: false },
  ],
  llmRuntime: {
    cache: { enabled: true, ttl_seconds: 900, max_entries: 256, origins: ["agent", "compact"] },
    circuit_breaker: { enabled: true, failure_threshold: 5, cooldown_seconds: 30 },
    retry: { wait_threshold_seconds: 20 },
  },
  catalog: {
    currentModel: "deepseek:deepseek-chat",
    overrides: { compact: "deepseek:deepseek-chat" },
    origins: ["agent", "compact", "title"],
    thinking: {
      model: "deepseek:deepseek-chat",
      configured: true,
      mode: "on",
      effort: "medium",
      supported_efforts: ["low", "medium", "high"],
    },
    tools: [
      {
        name: "fs_read",
        title: "读取文件",
        description: "读取工作区内的文件",
        declared_capabilities: ["workspace_read"],
        default_timeout_seconds: 30,
      },
    ],
    status: {
      session_id: "sess-1",
      mode: "workspace_write/always",
      last_event_id: "e21",
      workspace_roots: ["/Users/dev/repo"],
      model: "deepseek:deepseek-chat",
      busy: false,
    },
    recovery: {
      checkpoint_count: 1,
      pending: [{ checkpoint_id: "cp-2", status: "pending", created_at: "2026-09-08T09:30:00" }],
    },
  },
};

export const projects = [
  { project_id: "p-1", primary_workspace_root: "/Users/dev/repo", workspace_roots: ["/Users/dev/repo"] },
  { project_id: "p-2", primary_workspace_root: "/Users/dev/other", workspace_roots: ["/Users/dev/other"] },
];

export const planning = {
  markdown: "# 前端重构\n\n把结构收敛到一套布局。\n\n- 删死代码\n- 拆 App\n",
  plan: {
    plan_id: "plan-1",
    title: "前端重构",
    goal: "结构收敛",
    status: "proposed",
    steps: [
      { title: "删死代码", detail: "五个函数" },
      { title: "拆 App", detail: "按区块" },
    ],
  },
  todo: {
    items: [
      { title: "删死代码", status: "done" },
      { title: "拆 App", status: "pending" },
    ],
  },
};

export const planIndex = {
  active_plan_id: "plan-1",
  active_todo_id: "todo-1",
  plans: [
    { plan_id: "plan-1", revision: 2, status: "proposed", title: "前端重构", updated_at: "2026-09-08" },
    { plan_id: "plan-0", revision: 1, status: "archived", title: "旧计划", updated_at: "2026-09-01" },
  ],
};

export const markdownSample = [
  "# 一级标题",
  "",
  "普通段落，带 **加粗**、`行内代码` 和 [链接](https://example.com)。",
  "",
  "## 二级标题",
  "",
  "- 无序项一",
  "- 无序项二",
  "",
  "1. 有序项一",
  "2. 有序项二",
  "",
  "> 引用第一行",
  "> 引用第二行",
  "",
  "---",
  "",
  "```ts",
  "const a: number = 1;",
  "```",
].join("\n");
