import assert from "node:assert/strict";
import test from "node:test";

import {
  appendRunEvent,
  buildTimeline,
  categoryOf,
  groupToolEvents,
  metricsFor,
  newLocalTurn,
  restoreTurn,
  shouldAutoFollow,
  shouldSendOnEnter,
} from "../src/runModel.ts";
import { parseMarkdown } from "../src/markdownModel.ts";

function event(kind, sequence, payload = {}, extra = {}) {
  return { event_id: `e${sequence}`, kind, turn_id: "turn-1", sequence, payload, ...extra };
}

test("optimistic turn shows the user message before any server event", () => {
  const turn = newLocalTurn("立刻显示", 1000);
  assert.equal(turn.userText, "立刻显示");
  assert.equal(turn.status, "running");
  assert.equal(turn.events.length, 0);
});

test("model deltas stream in order and terminal event completes the turn", () => {
  let turns = [newLocalTurn("hello", 1000)];
  turns = appendRunEvent(turns, event("turn_started", 1));
  turns = appendRunEvent(turns, event("model_output_delta", 2, { text: "你" }, { request_id: "r1" }));
  turns = appendRunEvent(turns, event("model_output_delta", 3, { text: "好" }, { request_id: "r1" }));
  turns = appendRunEvent(turns, event("model_completed", 4, { tool_call_count: 0 }, { request_id: "r1" }));
  turns = appendRunEvent(turns, event("turn_completed", 5, { elapsed_ms: 230, model_calls: 1, tool_calls: 0 }));
  assert.equal(turns[0].turnId, "turn-1");
  assert.equal(turns[0].assistantText, "你好");
  assert.equal(turns[0].status, "completed");
});

test("the answer keeps growing while the answering call is still streaming", () => {
  let turns = [newLocalTurn("hello", 1000)];
  turns = appendRunEvent(turns, event("model_output_delta", 1, { text: "先检查文件" }, { request_id: "r1" }));
  turns = appendRunEvent(turns, event("model_completed", 2, { tool_call_count: 1 }, { request_id: "r1" }));
  turns = appendRunEvent(turns, event("model_started", 3, {}, { request_id: "r2" }));
  turns = appendRunEvent(turns, event("model_output_delta", 4, { text: "最终" }, { request_id: "r2" }));
  assert.equal(turns[0].assistantText, "", "还没表态之前, 正文只属于处理过程");
  turns = appendRunEvent(turns, event("model_completed", 5, { tool_call_count: 0 }, { request_id: "r2" }));
  assert.equal(turns[0].assistantText, "最终");
  // 非流式路径会在 completed 之后再补增量; 已认定的回答要接着长, 不能停在半句。
  turns = appendRunEvent(turns, event("model_output_delta", 6, { text: "回答" }, { request_id: "r2" }));
  assert.equal(turns[0].assistantText, "最终回答");
});

test("tool events are grouped by invocation and metrics retain CLI usage fields", () => {
  const events = [
    event("model_started", 1),
    event("model_usage", 2, { input_tokens: 10, output_tokens: 4, reasoning_tokens: 2, cached_tokens: 3, total_tokens: 14, estimated: true }),
    event("tool_queued", 3, { tool_name: "shell_run" }, { tool_call_id: "provider-call-1" }),
    event("tool_started", 4, { tool_name: "shell_run" }, { invocation_id: "i1" }),
    event("tool_completed", 5, { tool_name: "shell_run", elapsed_ms: 80 }, { invocation_id: "i1" }),
    event("turn_completed", 6, { elapsed_ms: 420, model_calls: 1, tool_calls: 1 }),
  ];
  assert.equal(groupToolEvents(events).length, 1);
  assert.deepEqual(metricsFor(events, 2000, 1000), {
    elapsedMs: 420, modelCalls: 1, toolCalls: 1,
    inputTokens: 10, outputTokens: 4, reasoningTokens: 2, cachedTokens: 3,
    totalTokens: 14, estimated: true,
  });
});

test("IME composition Enter does not send and scroll follow has a 140px dead zone", () => {
  assert.equal(shouldSendOnEnter("Enter", false, true, true), false);
  assert.equal(shouldSendOnEnter("Enter", false, false, false), true);
  assert.equal(shouldSendOnEnter("Enter", true, false, false), false);
  assert.equal(shouldAutoFollow(2000, 900, 900), false);
  assert.equal(shouldAutoFollow(2000, 970, 900), true);
});

test("chat and plan markdown preserve semantic blocks without raw HTML execution", () => {
  const blocks = parseMarkdown("# 标题\n\n1. 步骤\n   详细说明\n\n```python\nprint('hi')\n```\n\n<script>alert(1)</script>");
  assert.deepEqual(blocks.map((block) => block.kind), ["heading", "list", "code", "paragraph"]);
  assert.equal(blocks[1].items[0], "步骤 — 详细说明");
  assert.equal(blocks[2].text, "print('hi')");
  assert.equal(blocks[3].text, "<script>alert(1)</script>");
});

test("events for an unseen turn are adopted so a page reload keeps rendering the run", () => {
  let turns = appendRunEvent([], event("model_started", 1, {}, { request_id: "r1" }), 5000);
  assert.equal(turns.length, 1);
  assert.equal(turns[0].turnId, "turn-1");
  assert.equal(turns[0].userText, "");
  turns = appendRunEvent(turns, event("model_output_delta", 2, { text: "继续" }, { request_id: "r1" }));
  turns = appendRunEvent(turns, event("model_completed", 3, { tool_call_count: 0 }, { request_id: "r1" }));
  turns = appendRunEvent(turns, event("turn_completed", 4, { elapsed_ms: 12 }));
  assert.equal(turns.length, 1);
  assert.equal(turns[0].assistantText, "继续");
  assert.equal(turns[0].status, "completed");
});

test("a finished turn does not swallow events belonging to the next turn", () => {
  let turns = [newLocalTurn("第一轮", 1000)];
  turns = appendRunEvent(turns, event("turn_completed", 1));
  turns = appendRunEvent(turns, { ...event("turn_started", 1), event_id: "n1", turn_id: "turn-2" }, 6000);
  assert.equal(turns.length, 2);
  assert.equal(turns[1].turnId, "turn-2");
});

test("streaming deltas stay out of the event array so long answers do not stall the page", () => {
  let turns = [newLocalTurn("长回答", 1000)];
  turns = appendRunEvent(turns, event("model_started", 1, { call_index: 0 }, { request_id: "r1" }));
  const started = performance.now();
  for (let index = 0; index < 4000; index += 1) {
    turns = appendRunEvent(turns, event("model_output_delta", index + 2, { text: "字" }, { request_id: "r1" }));
  }
  const elapsed = performance.now() - started;
  turns = appendRunEvent(turns, event("model_completed", 4002, { tool_call_count: 0 }, { request_id: "r1" }));
  assert.equal(turns[0].events.length, 2);
  assert.equal(turns[0].assistantText.length, 4000);
  assert.equal(turns[0].outputs.r1.length, 4000);
  assert.ok(elapsed < 400, `4000 条增量耗时 ${elapsed.toFixed(0)}ms，说明又退回了平方级开销`);
});

test("replayed events are dropped by turn sequence, and StrictMode double runs stay idempotent", () => {
  let turns = [newLocalTurn("去重", 1000)];
  turns = appendRunEvent(turns, event("model_started", 1, {}, { request_id: "r1" }));
  const once = appendRunEvent(turns, event("model_output_delta", 2, { text: "好" }, { request_id: "r1" }));
  const twice = appendRunEvent(turns, event("model_output_delta", 2, { text: "好" }, { request_id: "r1" }));
  assert.equal(once[0].outputs.r1, "好");
  assert.equal(twice[0].outputs.r1, "好");
  assert.equal(appendRunEvent(once, event("model_output_delta", 2, { text: "好" }, { request_id: "r1" })), once);
});

test("a server snapshot rebuilds a finished process so a refreshed page can still open it", () => {
  const turn = restoreTurn({
    turn_id: "turn-1",
    events: [
      event("model_started", 1, {}, { request_id: "r1" }),
      event("tool_completed", 2, { tool_name: "fs_read", status: "ok", elapsed_ms: 12 }, { invocation_id: "i1" }),
      event("turn_completed", 3, { elapsed_ms: 900, model_calls: 1, tool_calls: 1 }),
    ],
    outputs: { r1: "中间正文" },
  }, 5000);
  assert.equal(turn.status, "completed");
  assert.equal(turn.turnId, "turn-1");
  assert.equal(turn.outputs.r1, "中间正文");
  assert.equal(metricsFor(turn.events, 6000, 5000).toolCalls, 1);
});

test("a half-streamed list marker must not hang the parser", () => {
  // 回归: `1. ` 这种中间态原来会让外层 while 原地打转并无限 push, 标签页被 OOM 杀掉。
  for (const partial of ["1. ", "- ", "* ", "+ ", "1) ", "  - "]) {
    const blocks = parseMarkdown(partial);
    assert.ok(blocks.length <= 1, `${JSON.stringify(partial)} 解析出了 ${blocks.length} 个块`);
  }
  assert.deepEqual(parseMarkdown("1. 第一项\n2. 第二项").map((b) => b.kind), ["list"]);
  assert.deepEqual(parseMarkdown("- 一\n- 二")[0].items, ["一", "二"]);
});

test("every streaming prefix of a markdown answer parses in bounded time", () => {
  const answer = "# 标题\n\n先说结论。\n\n1. 第一步\n2. 第二步\n\n```bash\nmake ci\n```\n\n- 要点一\n- 要点二\n";
  const started = performance.now();
  for (let cut = 0; cut <= answer.length; cut += 1) parseMarkdown(answer.slice(0, cut));
  assert.ok(performance.now() - started < 500, "逐字符前缀解析不该变慢");
});

test("narration never reaches the chat, not even for one frame", () => {
  // 回归: 正文以前先进最终回答, 等模型表态要调工具再撤回 —— 用户看到的是一段话在
  // 聊天里闪一下又消失。改成只有"这次调用不再要工具"才交给回答区, 中间输出一次都
  // 不进聊天。
  let turns = [newLocalTurn("改个文件", 1000)];
  turns = appendRunEvent(turns, event("model_started", 1, { call_index: 0 }, { request_id: "r1" }));
  turns = appendRunEvent(turns, event("model_output_delta", 2, { text: "让我先看看目录" }, { request_id: "r1" }));
  assert.equal(turns[0].assistantText, "", "流的过程中回答区必须始终是空的");
  assert.equal(turns[0].outputs.r1, "让我先看看目录", "正文只在处理过程里");

  turns = appendRunEvent(turns, event("model_completed", 3, { tool_call_count: 1 }, { request_id: "r1" }));
  assert.equal(turns[0].assistantText, "");
  assert.equal(turns[0].answerRequestId, null);

  turns = appendRunEvent(turns, event("model_started", 4, { call_index: 1 }, { request_id: "r2" }));
  turns = appendRunEvent(turns, event("model_output_delta", 5, { text: "改好了" }, { request_id: "r2" }));
  assert.equal(turns[0].assistantText, "");
  turns = appendRunEvent(turns, event("model_completed", 6, { tool_call_count: 0 }, { request_id: "r2" }));
  assert.equal(turns[0].assistantText, "改好了", "最后一次调用没有工具, 它就是答案");
  assert.equal(turns[0].answerRequestId, "r2");
});

test("a restored turn knows which model call was the answer", () => {
  // 回答已经在 transcript 里; 处理过程再展示一遍, 刷新后同一段文字会出现两次。
  const turn = restoreTurn({
    turn_id: "turn-1",
    events: [
      event("model_completed", 2, { tool_call_count: 2 }, { request_id: "r1" }),
      event("model_completed", 4, { tool_call_count: 0 }, { request_id: "r2" }),
      event("turn_completed", 5, {}),
    ],
    outputs: { r1: "先看看", r2: "结论" },
  }, 5000);
  assert.equal(turn.answerRequestId, "r2");
});

function toolCall(base, name, invocation, capabilities = ["workspace_read"]) {
  return [
    event("tool_queued", base, { tool_name: name }, { tool_call_id: invocation }),
    event("decision_summary", base + 1, { reason_summary: "模型请求 1 个工具调用" }),
    event("tool_prepared", base + 2, { tool_name: name, capabilities }, { invocation_id: invocation }),
    event("tool_completed", base + 3, { tool_name: name, status: "ok" }, { invocation_id: invocation }),
  ];
}

test("a tool is categorised by the capabilities it declared, not by its name", () => {
  // 回归: 分类靠一份写死的工具名清单, 里面的 fs.search_text 从来就不是真名 (search_text),
  // 于是每次搜索都被归进"其他工具" —— 而没有任何东西会因此报错。
  assert.equal(categoryOf("search_text", ["workspace_read"]).id, "read");
  assert.equal(categoryOf("mcp_acme_fetch_doc", ["external_read"]).id, "read");
  assert.equal(categoryOf("fs_edit_file", ["workspace_write"]).id, "write");
  // 同时声明读和写的调用算写入: 它的后果是写。
  assert.equal(categoryOf("fs_move", ["workspace_read", "path_move"]).id, "write");
  assert.equal(categoryOf("shell_run", ["execute_shell", "workspace_write"]).id, "shell");
  // 连 prepare 都没走到就没有能力可看, 退回按名字猜。清单只收**当前真实存在**的工具:
  // 早先它躺着 fs.create_file / fs.list_files 这些 ADR-0029 就删掉的名字。
  assert.equal(categoryOf("fs_apply_patch", []).id, "write");
  assert.equal(categoryOf("something_odd", []).id, "other");
  // 模型编出来的名字也以 fs_ 开头; 猜成"读取文件"就是在替一次没发生的写入洗白。
  assert.equal(categoryOf("fs_write_file", []).id, "other");
});

test("consecutive same-kind tool calls aggregate even with a decision between them", () => {
  // 回归: 每次派发前都有一条 decision_summary 夹在中间, 连着读三个文件因此没有一次是
  // "相邻"的, 时间线摊成六行 —— 而聚合本来就是为了不摊。
  const events = [
    event("model_started", 1, {}, { request_id: "r1" }),
    event("model_completed", 2, { tool_call_count: 3 }, { request_id: "r1" }),
    ...toolCall(10, "fs_read", "i1"),
    ...toolCall(20, "fs_read", "i2"),
    ...toolCall(30, "fs_scan_tree", "i3"),
    ...toolCall(40, "shell_run", "i4", ["execute_shell"]),
  ];
  const timeline = buildTimeline(events);
  assert.deepEqual(
    timeline.map((item) => `${item.kind}:${item.kind === "tools" ? item.category.id : ""}`),
    ["model:", "tools:read", "tools:shell"],
  );
  assert.equal(timeline[1].groups.length, 3, "三次读取合成一段");
  assert.equal(timeline[1].reason, "模型请求 1 个工具调用", "决策摘要挂到它解释的那一步上");
});

test("tool calls separated by a model call are not merged across it", () => {
  // 只在"工具列表"里判相邻, 会把隔着一次模型调用的两次读文件也合起来, 合出来的那段
  // 还带着更早的 sequence, 于是排到模型调用前面 —— 时间线说了假话。
  const events = [
    ...toolCall(10, "fs_read", "i1"),
    event("model_started", 20, {}, { request_id: "r2" }),
    event("model_completed", 21, { tool_call_count: 1 }, { request_id: "r2" }),
    ...toolCall(30, "fs_read", "i2"),
  ];
  assert.deepEqual(buildTimeline(events).map((item) => item.kind), ["tools", "model", "tools"]);
});

test("an unregistered tool still shows what the model passed in", () => {
  // 回归: fs_write_file 连 prepare 都没走到, 页面上只剩一个工具名 —— 而"模型到底传了
  // 什么"正是这类失败唯一值得看的东西。
  const events = [
    event("tool_queued", 1, { tool_name: "fs_write_file", arguments: [["path", "a.py"], ["content", "x"]] }, { tool_call_id: "c1" }),
    event("tool_completed", 2, { tool_name: "fs_write_file", status: "tool_unavailable", executed: false, error_code: "tool_unavailable" }, { invocation_id: "i1" }),
  ];
  const timeline = buildTimeline(events);
  assert.equal(timeline.length, 1);
  const [group] = timeline[0].groups;
  assert.deepEqual(group.events[0].payload.arguments, [["path", "a.py"], ["content", "x"]]);
  assert.equal(group.events.at(-1).payload.executed, false);
});
