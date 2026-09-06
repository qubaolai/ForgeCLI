import assert from "node:assert/strict";
import { after, test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

const server = await createServer({ server: { middlewareMode: true, ws: false }, optimizeDeps: { noDiscovery: true, include: [] }, appType: "custom" });
after(() => server.close());
const { QuestionCard } = await server.ssrLoadModule("/src/QuestionCard.tsx");
const prompt = {
  prompt_id: "q1", kind: "question", title: "实现哪些阶段？", body: "选择需要实现的阶段",
  free_text: true, allow_skip: true, recommended_option_id: "one", detail: {},
  choices: [
    { value: "one", label: "实现阶段 1", detail: "完善卡片" },
    { value: "two", label: "实现阶段 2", detail: "结构化接口" },
    { value: "three", label: "实现阶段 3", detail: "即时推送" },
  ],
};
for (const mode of ["single", "multiple"]) {
  test(`renders backend ${mode} choices with one recommendation and explicit skip`, () => {
    const html = renderToStaticMarkup(createElement(QuestionCard, {
      prompt: { ...prompt, selection_mode: mode }, onResolve: async () => {},
    }));
    assert.equal((html.match(new RegExp(`type="${mode === "single" ? "radio" : "checkbox"}"`, "g")) ?? []).length, 3);
    assert.ok(!html.includes(`type="${mode === "single" ? "checkbox" : "radio"}"`));
    assert.equal((html.match(/<em>推荐<\/em>/g) ?? []).length, 1);
    for (const option of prompt.choices) assert.ok(html.includes(option.detail));
    assert.ok(html.includes("跳过本题"));
    assert.ok(!html.includes("全部不回答"));
    assert.ok(!html.includes("LLM 推荐"));
    assert.ok(!html.includes("checked="), "recommendation must not preselect a choice");
    assert.match(html, /disabled="">提交回答/);
  });
}
test("question and option text is escaped, including descriptions", () => {
  const html = renderToStaticMarkup(createElement(QuestionCard, {
    prompt: { ...prompt, title: "<script>alert(1)</script>", choices: [{ value: "x", label: "<img>", detail: "<iframe>" }] },
    onResolve: async () => {},
  }));
  assert.ok(!html.includes("<script>"));
  assert.ok(html.includes("&lt;iframe&gt;"));
});
