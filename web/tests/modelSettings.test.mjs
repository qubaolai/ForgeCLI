import assert from "node:assert/strict";
import test from "node:test";

import {
  buildInitialModelParams,
  modelPlaceholder,
  modelRef,
  providerAvailabilityCopy,
  splitModelRef,
} from "../src/modelSettings.ts";

test("model refs preserve local model ids containing colons", () => {
  const ref = modelRef("local", "qwen3:8b");
  assert.equal(ref, "local:qwen3:8b");
  assert.deepEqual(splitModelRef(ref), ["local", "qwen3:8b"]);
});

test("provider availability explains the action the user needs", () => {
  assert.equal(providerAvailabilityCopy({ available: true, api_key_env: "DEEPSEEK_API_KEY" }), "密钥已就绪");
  assert.equal(
    providerAvailabilityCopy({ available: false, api_key_env: "OPENAI_API_KEY" }),
    "缺少 OPENAI_API_KEY",
  );
  assert.equal(providerAvailabilityCopy({ available: true, api_key_env: "" }), "无需密钥");
});

test("the add-model form sends only valid optional numeric fields", () => {
  // 上下文窗口与最大输出留空 = 用供应商默认值, 所以它们是可选的。
  assert.deepEqual(buildInitialModelParams("", ""), { temperature: 0.7, top_p: 1, thinking_mode: "off" });
  assert.throws(() => buildInitialModelParams("64k", ""), /上下文窗口必须是正整数/);
  assert.throws(() => buildInitialModelParams("", "0"), /最大输出 Tokens必须是正整数/);
});

test("sampling parameters are always sent, never left to the provider", () => {
  // 留空等于让每家供应商用自己的默认值, 而那些默认值互不相同 —— 同一个提示词换个
  // 供应商就得到不同的发散程度, 而配置里看不出为什么。
  assert.deepEqual(buildInitialModelParams("64000", "8192"), {
    context_window: 64000,
    max_tokens: 8192,
    temperature: 0.7,
    top_p: 1,
    thinking_mode: "off",
  });
});

test("thinking is part of adding a model, not a later separate edit", () => {
  assert.deepEqual(
    buildInitialModelParams("", "", {
      temperature: "0.2",
      topP: "0.9",
      thinkingMode: "on",
      thinkingEffort: "high",
    }),
    { temperature: 0.2, top_p: 0.9, thinking_mode: "on", thinking_effort: "high" },
  );
});

test("an out-of-range sampling value is refused before it reaches the backend", () => {
  assert.throws(
    () =>
      buildInitialModelParams("", "", {
        temperature: "3",
        topP: "1.0",
        thinkingMode: "off",
        thinkingEffort: "",
      }),
    /温度必须是 0 到 2 之间的数字/,
  );
  assert.throws(
    () =>
      buildInitialModelParams("", "", {
        temperature: "0.7",
        topP: "2",
        thinkingMode: "off",
        thinkingEffort: "",
      }),
    /top_p必须是 0 到 1 之间的数字/,
  );
});

test("known providers have contextual model examples", () => {
  assert.equal(modelPlaceholder("deepseek"), "例如 deepseek-chat");
  assert.equal(modelPlaceholder("local"), "例如 qwen3:8b");
  assert.match(modelPlaceholder("custom"), /模型 ID/);
});
