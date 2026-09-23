import assert from "node:assert/strict";
import { after, test } from "node:test";
import { mkdtemp, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import ts from "typescript";

// Exercise the real reducers with Node's test runner, using the existing TS compiler.
const temp = await mkdtemp(join(tmpdir(), "forge-run-tests-"));
after(() => rm(temp, { recursive: true, force: true }));
const source = new URL("../src/shared/lib/run/", import.meta.url);
for (const file of await readdir(source)) {
  if (!file.endsWith(".ts")) continue;
  const text = await readFile(new URL(file, source), "utf8");
  const { outputText } = ts.transpileModule(text, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
  });
  await writeFile(
    join(temp, file.replace(/\.ts$/, ".mjs")),
    outputText.replaceAll(/@\/shared\/lib\/run\/([\w-]+)/g, "./$1.mjs"),
  );
}
const { newLocalTurn, appendRunEvent, restoreTurn } = await import(pathToFileURL(join(temp, "turn.mjs")));
const { metricsFor } = await import(pathToFileURL(join(temp, "metrics.mjs")));
const { buildTimeline } = await import(pathToFileURL(join(temp, "timeline.mjs")));
const { activityOf } = await import(pathToFileURL(join(temp, "activity.mjs")));
const event = (kind, sequence, payload = {}, agent_id = "", request_id = "root") => ({
  event_id: `e${sequence}`,
  session_id: "s",
  turn_id: "t",
  kind,
  sequence,
  payload,
  agent_id,
  request_id,
});

test("child final output never replaces parent answer or turn status", () => {
  let turns = [newLocalTurn("research")];
  const events = [
    event("model_output_delta", 1, { text: "parent" }),
    event("model_output_delta", 2, { text: "child" }, "a", "child-call"),
    event("model_completed", 3, { tool_call_count: 0 }, "a", "child-call"),
    event("sub_agent_finished", 4, { status: "completed" }, "a"),
  ];
  for (const e of events) turns = appendRunEvent(turns, e);
  assert.equal(turns[0].assistantText, "");
  assert.equal(turns[0].status, "running");
  assert.equal(restoreTurn({ turn_id: "t", events }).answerRequestId, null);
  turns = appendRunEvent(turns, event("model_completed", 5, { tool_call_count: 0 }));
  assert.equal(turns[0].assistantText, "parent");
});

test("usage includes children once and timeline keeps them off the root", () => {
  const events = [
    event("model_started", 1),
    event("model_usage", 2, { total_tokens: 10 }),
    event("model_started", 3, {}, "a", "c"),
    event("model_usage", 4, { total_tokens: 15 }, "a", "c"),
    event("sub_agent_finished", 5, { total_tokens: 15 }, "a"),
    event("turn_completed", 6, { model_calls: 1 }),
  ];
  assert.equal(metricsFor(events).totalTokens, 25);
  assert.equal(metricsFor(events).modelCalls, 2);
  assert.equal(buildTimeline(events).filter((item) => item.kind === "model").length, 1);
});

test("delayed child cleanup remains visible until that child exits", () => {
  const turn = { ...newLocalTurn("research"), events: [event("sub_agent_stopping", 1, {}, "a")] };
  assert.equal(activityOf(turn), "子任务正在停止…");
  turn.events.push(event("sub_agent_finished", 2, { status: "cancelled" }, "a"));
  assert.notEqual(activityOf(turn), "子任务正在停止…");
});
