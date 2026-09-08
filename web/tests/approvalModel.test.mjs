import assert from "node:assert/strict";
import test from "node:test";

import { load } from "./load.mjs";

const { canLearn, defaultOpenSections, headlineOf, isConsequential, learnHintOf, severityOf } = await load(
  "/src/shared/lib/approval.ts",
);

const LABELS = ["读取", "写入", "删除", "移动", "网络", "外部副作用"];

/** 后端 counts 含为零的类别, 所以替身也必须含 —— 少一行就测不到"零不等于没提"。 */
function approval({
  counts = {},
  mandatory = false,
  unresolved = null,
  scopes = ["once"],
  blocked = "",
  scripts = [],
  previews = [],
} = {}) {
  return {
    approval_id: "ap_1",
    mandatory,
    view: {
      mode: "auto",
      tool_name: "shell_run",
      workspace_roots: ["/w"],
      raw_command: "pytest -q",
      target_resolution: unresolved ? "dynamic" : "static",
      target_groups: LABELS.map((label) => ({
        label,
        paths: Array.from({ length: counts[label] ?? 0 }, (_, i) => `/w/${label}${i}`),
      })),
      script_snapshots: scripts,
      content_previews: previews,
      counts: LABELS.map((label) => ({ label, count: counts[label] ?? 0 })),
      unresolved_reason: unresolved,
      allowed_scopes: scopes,
      learn_blocked_reason: blocked,
    },
  };
}

// ---- 危险程度 ----

test("a read-only closed-target call is the calm tier", () => {
  assert.equal(severityOf(approval({ counts: { 读取: 12 } })), "calm");
});

test("writing or moving or reaching the network raises it to caution", () => {
  assert.equal(severityOf(approval({ counts: { 写入: 1 } })), "caution");
  assert.equal(severityOf(approval({ counts: { 移动: 1 } })), "caution");
  assert.equal(severityOf(approval({ counts: { 网络: 1 } })), "caution");
});

test("deleting anything is critical even when everything else is quiet", () => {
  assert.equal(severityOf(approval({ counts: { 删除: 1 } })), "critical");
});

test("an external irreversible effect is critical", () => {
  assert.equal(severityOf(approval({ counts: { 外部副作用: 1 } })), "critical");
});

test("mandatory approval is critical regardless of what it touches", () => {
  assert.equal(severityOf(approval({ mandatory: true })), "critical");
});

test("an unclosed target set is critical because the counts cannot be trusted", () => {
  assert.equal(severityOf(approval({ unresolved: "$ROOT 运行时展开" })), "critical");
});

test("severity only ever rises: a write plus a delete is still critical", () => {
  assert.equal(severityOf(approval({ counts: { 写入: 4, 删除: 1 } })), "critical");
});

// ---- 标题 ----

test("the headline states the consequence, not the tool", () => {
  assert.equal(headlineOf(approval({ counts: { 删除: 3 } })), "要删除 3 个路径");
  assert.equal(headlineOf(approval({ counts: { 写入: 1, 移动: 1 } })), "要改 2 个文件");
  assert.equal(headlineOf(approval({ counts: { 读取: 9 } })), "要跑一条只读命令");
  assert.equal(headlineOf(approval({ mandatory: true })), "这一步必须你亲自确认");
});

// ---- 始终允许 ----

test("the always-allow button needs the workspace scope", () => {
  assert.equal(canLearn(approval({ scopes: ["once"] })), false);
  assert.equal(canLearn(approval({ scopes: ["once", "workspace"] })), true);
});

test("when the button is unavailable the hint carries the backend reason", () => {
  const hint = learnHintOf(approval({ scopes: ["once"], blocked: "拿不到可执行文件身份, 规则绑不住" }));
  assert.match(hint, /拿不到可执行文件身份/);
});

test("a missing reason still says something rather than going silent", () => {
  // 静默消失正是这次要修的缺陷: 没有理由也不能什么都不说。
  assert.notEqual(learnHintOf(approval({ scopes: ["once"], blocked: "" })).trim(), "");
});

test("when it is available the hint says the rule is not the literal command", () => {
  const hint = learnHintOf(approval({ scopes: ["once", "workspace"] }));
  assert.match(hint, /不是这行命令的字面量/);
});

// ---- 证据区 ----

test("a read-only closed call lists no targets", () => {
  assert.equal(isConsequential(approval({ counts: { 读取: 5 } })), false);
});

test("anything beyond reading brings the target list back", () => {
  assert.equal(isConsequential(approval({ counts: { 读取: 5, 写入: 1 } })), true);
  assert.equal(isConsequential(approval({ unresolved: "无法枚举" })), true);
});

test("script bodies and write previews open by default, target lists do not", () => {
  const open = defaultOpenSections(
    approval({
      scripts: [{ language: "bash", origin: "inline", path: "", source: "rm -rf x" }],
      previews: [{ path: "a.py", content: "x = 1", truncated: false }],
      counts: { 写入: 1 },
    }),
  );
  assert.deepEqual(open, { scripts: true, previews: true, targets: false });
});

test("nothing opens when there is no evidence to show", () => {
  assert.deepEqual(defaultOpenSections(approval()), { scripts: false, previews: false, targets: false });
});
