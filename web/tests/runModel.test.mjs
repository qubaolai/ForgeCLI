import assert from "node:assert/strict";
import test from "node:test";

import {
  activityOf,
  appendRunEvent,
  buildTimeline,
  categoryOf,
  formatTokens,
  groupModelEvents,
  groupToolEvents,
  metricsFor,
  newLocalTurn,
  summariseTools,
  toolActionLabel,
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
    totalTokens: 14, compactTokens: 0, estimated: true,
  });
});

test("context compaction is counted in the turn total and broken out separately", () => {
  // 回归: 压缩那次模型调用的 usage 被原地丢掉, 于是本轮合计少了一大块 —— 而它的
  // input 大致等于被压掉的那段历史, 不是零头 (ADR-0037)。
  const events = [
    event("model_started", 1),
    event("model_usage", 2, { origin: "act", input_tokens: 10, output_tokens: 4, total_tokens: 14 }),
    event("context_compacted", 3, { level: "summary", tokens_before: 9000, tokens_after: 1200, tokens_saved: 7800, messages_replaced: 12 }),
    event("model_usage", 4, { origin: "compact", input_tokens: 8000, output_tokens: 200, total_tokens: 8200 }, { request_id: "r-compact" }),
    event("turn_completed", 5, { elapsed_ms: 100, model_calls: 1, tool_calls: 0 }),
  ];
  const metrics = metricsFor(events, 2000, 1000);
  assert.equal(metrics.totalTokens, 14 + 8200, "压缩的花费必须进合计");
  assert.equal(metrics.compactTokens, 8200, "同时要能单独看出压缩占了多少");
  assert.equal(metrics.modelCalls, 1, "压缩不是 Agent 自己的一次模型调用");
});

test("compaction usage does not become a phantom model step", () => {
  // 它带着自己的 request_id, 不挡掉就会在时间线上多出一个只有用量、没有开始也没有
  // 结束的"模型调用", 而且永远显示成运行中。
  const events = [
    event("model_started", 1, {}, { request_id: "r1" }),
    event("model_completed", 2, { tool_call_count: 0, text_chars: 12 }, { request_id: "r1" }),
    event("context_compacted", 3, { level: "summary", tokens_saved: 100, messages_replaced: 2 }),
    event("model_usage", 4, { origin: "compact", total_tokens: 500 }, { request_id: "r-compact" }),
  ];
  assert.equal(groupModelEvents(events).length, 1);
  const timeline = buildTimeline(events, {}, { r1: "最终回答" });
  assert.deepEqual(timeline.map((item) => item.kind), ["model", "note"]);
});

test("token counts switch to k at a thousand", () => {
  assert.equal(formatTokens(0), "0");
  assert.equal(formatTokens(999), "999");
  assert.equal(formatTokens(1000), "1k");
  assert.equal(formatTokens(1234), "1.2k");
  assert.equal(formatTokens(12345), "12.3k");
  // 1.0k 看起来像是精确到百位, 其实不是。
  assert.equal(formatTokens(156000), "156k");
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

/**
 * 后端 `/api/v1/tools` 的 `all` 索引之后的形状。写成固定值而不是去拉接口: 这些用例
 * 钉的是"查得到就用 title, 查不到就退回工具名"这条规则, 不是后端此刻叫什么名字。
 */
const DIRECTORY = {
  todo_set_status: { title: "更新待办状态", action: "process" },
  todo_write: { title: "重写待办", action: "process" },
  shell_run: { title: "执行 Shell 命令", action: "execute" },
  fs_apply_patch: { title: "应用补丁", action: "write" },
  search_text: { title: "搜索文本", action: "locate_text" },
  memory_write: { title: "记住一件事", action: "memory" },
};

test("a tool is categorised by the capabilities it declared, not by its name", () => {
  // 回归: 分类靠一份写死的工具名清单, 里面的 fs.search_text 从来就不是真名 (search_text),
  // 于是每次搜索都被归进"其他工具" —— 而没有任何东西会因此报错。
  assert.equal(categoryOf("search_text", ["workspace_read"]).id, "read");
  assert.equal(categoryOf("mcp_acme_fetch_doc", ["external_read"]).id, "read");
  assert.equal(categoryOf("fs_edit_file", ["workspace_write"]).id, "write");
  // 同时声明读和写的调用算写入: 它的后果是写。
  assert.equal(categoryOf("fs_move", ["workspace_read", "path_move"]).id, "write");
  assert.equal(categoryOf("shell_run", ["execute_shell", "workspace_write"]).id, "shell");
  // 连 prepare 都没走到就没有能力可看, 这时查后端目录里的动作。目录是后端发的, 不再是
  // 一份手抄清单 —— 早先那份躺着 fs.create_file / fs.list_files 这些 ADR-0029 就删掉的名字。
  assert.equal(categoryOf("fs_apply_patch", [], DIRECTORY).id, "write");
  assert.equal(categoryOf("search_text", [], DIRECTORY).id, "read");
  assert.equal(categoryOf("memory_write", [], DIRECTORY).id, "memory");
  assert.equal(categoryOf("something_odd", [], DIRECTORY).id, "other");
  // 模型编出来的名字也以 fs_ 开头; 猜成"读取文件"就是在替一次没发生的写入洗白。
  assert.equal(categoryOf("fs_write_file", [], DIRECTORY).id, "other");
  // 目录还没拉回来时一律归"其他工具", 不猜。
  assert.equal(categoryOf("fs_apply_patch", []).id, "other");
});

test("consecutive same-kind tool calls aggregate even with a decision between them", () => {
  // 回归: 每次派发前都有一条 decision_summary 夹在中间, 连着读三个文件因此没有一次是
  // "相邻"的, 时间线摊成六行 —— 而聚合本来就是为了不摊。
  const events = [
    event("model_started", 1, {}, { request_id: "r1" }),
    event("model_completed", 2, { tool_call_count: 3, text_chars: 20 }, { request_id: "r1" }),
    ...toolCall(10, "fs_read", "i1"),
    ...toolCall(20, "fs_read", "i2"),
    ...toolCall(30, "fs_scan_tree", "i3"),
    ...toolCall(40, "shell_run", "i4", ["execute_shell"]),
  ];
  const timeline = buildTimeline(events, {}, { r1: "我来看几个文件" });
  assert.deepEqual(
    timeline.map((item) => `${item.kind}:${item.kind === "tools" ? item.category.id : ""}`),
    ["model:", "tools:read", "tools:shell"],
  );
  assert.equal(timeline[1].groups.length, 3, "三次读取合成一段");
  assert.equal(timeline[1].reason, "模型请求 1 个工具调用", "决策摘要挂到它解释的那一步上");
});

test("tool calls separated by something the model said are not merged across it", () => {
  // 只在"工具列表"里判相邻, 会把隔着一段叙述的两次读文件也合起来, 合出来的那段还带着
  // 更早的 sequence, 于是排到叙述前面 —— 时间线说了假话。
  const events = [
    ...toolCall(10, "fs_read", "i1"),
    event("model_started", 20, {}, { request_id: "r2" }),
    event("model_completed", 21, { tool_call_count: 1 }, { request_id: "r2" }),
    ...toolCall(30, "fs_read", "i2"),
  ];
  const timeline = buildTimeline(events, {}, { r2: "接着看下一个" });
  assert.deepEqual(timeline.map((item) => item.kind), ["tools", "model", "tools"]);
});

test("a silent model call does not break the run of tool calls around it", () => {
  // 这个循环一次只派发一个工具调用, 而模型常常一个字都不说就要下一个工具。那种节点
  // 渲染不出任何东西, 却横在中间让相邻合并永远不成立 —— 实测一屏 18 次调用一次都没并
  // 起来。它不进时间线, "模型跑了几次"由底部指标行回答。
  const events = [
    ...toolCall(10, "fs_read", "i1"),
    event("model_started", 20, {}, { request_id: "r2" }),
    event("model_completed", 21, { tool_call_count: 1, text_chars: 1 }, { request_id: "r2" }),
    ...toolCall(30, "fs_read", "i2"),
  ];
  // 实测: 模型在工具调用旁边会吐一个空白字符, 于是 text_chars=1 而屏幕上什么都没有。
  // 判据看的是渲染器用的那份 outputs, 不是这个计数。
  assert.deepEqual(buildTimeline(events, {}, { r2: " " }).map((item) => item.kind), ["tools"]);
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

// ---- 折叠状态下的进度行 ----
//
// 处理过程默认收起, 所以这一行是运行期间用户唯一看得到的进度。它说错了没有任何东西
// 会报错 —— 只会让人对着"正在处理"猜。

function running(...events) {
  let turns = [newLocalTurn("做点事", 1000)];
  for (const item of events) turns = appendRunEvent(turns, item);
  return turns[0];
}

test("the collapsed line separates thinking from generating", () => {
  const thinking = running(
    event("model_started", 1, {}, { request_id: "r1" }),
    event("model_reasoning_status", 2, { status: "started" }, { request_id: "r1" }),
  );
  assert.equal(activityOf(thinking), "模型思考中…");

  const generating = running(
    event("model_started", 1, {}, { request_id: "r1" }),
    event("model_reasoning_status", 2, { status: "started" }, { request_id: "r1" }),
    event("model_reasoning_status", 3, { status: "finished" }, { request_id: "r1" }),
  );
  assert.equal(activityOf(generating), "模型生成中…");
});

test("a running tool names the actual operation, not the tool id", () => {
  const turn = running(
    event("tool_started", 1, { tool_name: "todo_set_status" }, { tool_call_id: "c1" }),
  );
  assert.equal(activityOf(turn, DIRECTORY), "正在更新待办状态");
});

test("a finished tool stops being the current activity", () => {
  const turn = running(
    event("tool_started", 1, { tool_name: "shell_run" }, { tool_call_id: "c1" }),
    event("tool_completed", 2, { tool_name: "shell_run" }, { tool_call_id: "c1" }),
    event("model_started", 3, {}, { request_id: "r2" }),
  );
  assert.equal(activityOf(turn), "模型生成中…");
});

test("waiting on a human outranks whatever else is open", () => {
  const turn = running(
    event("tool_started", 1, { tool_name: "shell_run" }, { tool_call_id: "c1" }),
    event("approval_requested", 2, {}, { tool_call_id: "c1" }),
  );
  assert.equal(activityOf(turn), "等待你的审批");

  const resolved = running(
    event("tool_started", 1, { tool_name: "shell_run" }, { tool_call_id: "c1" }),
    event("approval_requested", 2, {}, { tool_call_id: "c1" }),
    event("approval_resolved", 3, {}, { tool_call_id: "c1" }),
  );
  assert.equal(resolved.status, "running");
  assert.equal(activityOf(resolved, DIRECTORY), "正在执行 Shell 命令");
});

test("a terminal turn reports its outcome instead of a live activity", () => {
  const turn = running(
    event("model_started", 1, {}, { request_id: "r1" }),
    event("turn_cancelled", 2, {}),
  );
  assert.equal(activityOf(turn), "处理已取消");
});

test("an unknown tool falls back to its own name rather than an invented action", () => {
  assert.equal(toolActionLabel("todo_write", DIRECTORY), "重写待办");
  assert.equal(toolActionLabel("mcp_acme_do_thing", DIRECTORY), "mcp_acme_do_thing");
  // 目录还没拉回来的那几帧: 显示工具名, 不显示一个猜出来的中文。
  assert.equal(toolActionLabel("todo_write"), "todo_write");
});

test("every model call's text stays in the flow, none of it is retracted", () => {
  // 最终回答必须流式, 而循环发请求之前分不出哪次是最后一次 —— 于是只能每次都流。
  // 流出来的叙述必然已经显示过, 所以不能再撤回它, 只能让它留下。
  //
  // 这条钉的是"留下": 两次调用的正文都要能从 outputs 里取到, 时间线也要给出两块。
  let turns = [newLocalTurn("改个文件", 1000)];
  turns = appendRunEvent(turns, event("model_started", 1, { call_index: 0 }, { request_id: "r1" }));
  turns = appendRunEvent(turns, event("model_output_delta", 2, { text: "我先看看目录" }, { request_id: "r1" }));
  turns = appendRunEvent(turns, event("model_completed", 3, { tool_call_count: 1, text_chars: 6 }, { request_id: "r1" }));
  turns = appendRunEvent(turns, event("tool_started", 4, { tool_name: "fs_find" }, { invocation_id: "i1" }));
  turns = appendRunEvent(turns, event("tool_completed", 5, { tool_name: "fs_find" }, { invocation_id: "i1" }));
  turns = appendRunEvent(turns, event("model_started", 6, { call_index: 1 }, { request_id: "r2" }));
  turns = appendRunEvent(turns, event("model_output_delta", 7, { text: "改好了" }, { request_id: "r2" }));
  turns = appendRunEvent(turns, event("model_completed", 8, { tool_call_count: 0, text_chars: 3 }, { request_id: "r2" }));

  const turn = turns[0];
  assert.equal(turn.outputs.r1, "我先看看目录", "叙述留在流里, 不被撤回");
  assert.equal(turn.outputs.r2, "改好了");
  // assistantText 仍然只认最终回答: 复制按钮与落盘用它, 那两处要的确实只是回答。
  assert.equal(turn.assistantText, "改好了");

  const kinds = buildTimeline(turn.events, {}, turn.outputs).map((item) => item.kind);
  assert.deepEqual(kinds, ["model", "tools", "model"], "叙述-工具-叙述, 按发生顺序");
});

test("a merged group shows only what it did, never which file", () => {
  // 收起时摆一个路径, 等于让一个随机挑出来的文件占满整行, 而它并不比另外几个更值得看。
  // 具体是哪几个展开就有。
  const directory = { fs_read: { title: "读取文件", action: "read" } };
  const call = (id, target) => ({ id, events: [
    event("tool_prepared", 1, { tool_name: "fs_read", targets: [target] }, { invocation_id: id }),
  ] });

  assert.deepEqual(summariseTools([call("a", "/x/y/Login.tsx")], directory),
    { text: "读取文件", mono: false, count: 1 });
  assert.deepEqual(
    summariseTools([call("a", "/x/A.tsx"), call("b", "/x/B.tsx")], directory),
    { text: "读取文件", mono: false, count: 2 },
  );
});

test("no separator dot and no path ever reach the collapsed line", () => {
  // 回归: 那一行曾经是"按符号名找定义 · /Users/.../SysUserServiceImpl.java" —— 一条
  // 一百多字符的绝对路径占满整行。
  const directory = { code_definitions: { title: "按符号名找定义", action: "locate_symbol" } };
  const summary = summariseTools([{ id: "a", events: [
    event("tool_prepared", 1, {
      tool_name: "code_definitions",
      arguments: [["symbol", "SysUserServiceImpl"]],
      targets: ["/Users/almond/Desktop/tmp_ant/test/backend/src/main/java/SysUserServiceImpl.java"],
    }, { invocation_id: "i1" }),
  ] }], directory);

  assert.equal(summary.text, "按符号名找定义");
  assert.ok(!summary.text.includes("·"), "不要点符号分割");
  assert.ok(!summary.text.includes("/"), "路径一个字符都不该上这一行");
});

test("a single shell command stays on the line, because it is not a path", () => {
  const directory = { shell_run: { title: "执行 Shell 命令", action: "execute" } };
  const summary = summariseTools([{ id: "a", events: [
    event("tool_prepared", 1, { tool_name: "shell_run", arguments: [["command", "mvn -q compile"]] }, { invocation_id: "i1" }),
  ] }], directory);

  assert.deepEqual(summary, { text: "mvn -q compile", mono: true, count: 1 });
});

test("the count is a separate field, never spliced into the label", () => {
  // 拼进文字要做"读取文件"→"读取 6 个文件"的动宾拆分, 而那对"执行 Shell 命令"
  // "按符号名找定义"这类标题根本拆不开。
  const directory = { fs_find: { title: "定位文件与认识目录", action: "locate_path" } };
  const summary = summariseTools(
    [1, 2, 3].map((n) => ({ id: `g${n}`, events: [event("tool_prepared", n, { tool_name: "fs_find" }, { invocation_id: `i${n}` })] })),
    directory,
  );

  assert.equal(summary.text, "定位文件与认识目录");
  assert.equal(summary.count, 3);
});

test("an unknown tool falls back to a count rather than inventing a verb", () => {
  // 目录里没有它就没有可信的名字。编一个动词出来会让一次没发生过的操作看起来发生过。
  const summary = summariseTools([
    { id: "a", events: [event("tool_prepared", 1, { tool_name: "mcp_acme_x" }, { invocation_id: "i1" })] },
    { id: "b", events: [event("tool_prepared", 2, { tool_name: "mcp_acme_y" }, { invocation_id: "i2" })] },
  ]);

  assert.equal(summary.text, "工具调用");
  assert.equal(summary.count, 2);
});
test("different tools merged into one line still name their kinds", () => {
  // 同一类的工具会被并进一组。退化成"3 次工具调用"就把信息全丢了。
  const directory = {
    fs_read: { title: "读取文件", action: "read" },
    search_text: { title: "搜索文本", action: "locate_text" },
  };
  const summary = summariseTools([
    { id: "a", events: [event("tool_prepared", 1, { tool_name: "fs_read" }, { invocation_id: "i1" })] },
    { id: "b", events: [event("tool_prepared", 2, { tool_name: "search_text" }, { invocation_id: "i2" })] },
    { id: "c", events: [event("tool_prepared", 3, { tool_name: "fs_read" }, { invocation_id: "i3" })] },
  ], directory);

  assert.equal(summary.text, "读取文件 / 搜索文本");
  assert.equal(summary.count, 3);
});

test("merging is by action, not by the coarser category", () => {
  // 四种定位与读取全都属于"读取文件"这一类。按类别并会把 6 次 fs_find 与 10 次 fs_read
  // 压成一行"18 次工具调用" —— 从平铺一个极端跳到另一个极端。
  const directory = {
    fs_find: { title: "定位文件与认识目录", action: "locate_path" },
    fs_read: { title: "读取文件", action: "read" },
  };
  const events = [
    ...toolCall(10, "fs_find", "i1"),
    ...toolCall(20, "fs_find", "i2"),
    ...toolCall(30, "fs_read", "i3"),
    ...toolCall(40, "fs_read", "i4"),
  ];

  const timeline = buildTimeline(events, directory);

  assert.equal(timeline.length, 2, "定位归定位, 读取归读取");
  assert.deepEqual(timeline.map((item) => item.groups.length), [2, 2]);
  assert.deepEqual(summariseTools(timeline[0].groups, directory), { text: "定位文件与认识目录", mono: false, count: 2 });
  assert.deepEqual(summariseTools(timeline[1].groups, directory), { text: "读取文件", mono: false, count: 2 });
});
