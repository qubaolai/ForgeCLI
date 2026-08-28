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
  assert.equal(providerAvailabilityCopy({ available: false, api_key_env: "OPENAI_API_KEY" }), "缺少 OPENAI_API_KEY");
  assert.equal(providerAvailabilityCopy({ available: true, api_key_env: "" }), "无需密钥");
});

test("the add-model form sends only valid optional numeric fields", () => {
  assert.deepEqual(buildInitialModelParams("64000", "8192"), { context_window: 64000, max_tokens: 8192 });
  assert.deepEqual(buildInitialModelParams("", ""), {});
  assert.throws(() => buildInitialModelParams("64k", ""), /上下文窗口必须是正整数/);
  assert.throws(() => buildInitialModelParams("", "0"), /最大输出 Tokens必须是正整数/);
});

test("known providers have contextual model examples", () => {
  assert.equal(modelPlaceholder("deepseek"), "例如 deepseek-chat");
  assert.equal(modelPlaceholder("local"), "例如 qwen3:8b");
  assert.match(modelPlaceholder("custom"), /模型 ID/);
});
