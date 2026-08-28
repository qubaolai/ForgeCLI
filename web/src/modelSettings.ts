export type ProviderAvailability = {
  api_key_env?: string;
  available: boolean;
};

const modelExamples: Record<string, string> = {
  deepseek: "例如 deepseek-chat",
  openai: "例如 gpt-5",
  glm: "例如 glm-4.5",
  mimo: "例如 mimo-v2-flash",
  local: "例如 qwen3:8b",
};

export function modelRef(providerId: string, modelId: string): string {
  return `${providerId}:${modelId}`;
}

export function splitModelRef(ref: string): [string, string] {
  const [providerId, ...modelParts] = ref.split(":");
  return [providerId, modelParts.join(":")];
}

export function modelPlaceholder(providerId: string): string {
  return modelExamples[providerId] ?? "填写供应商 API 使用的模型 ID";
}

export function providerAvailabilityCopy(provider: ProviderAvailability): string {
  if (!provider.api_key_env) return "无需密钥";
  if (provider.available) return "密钥已就绪";
  return `缺少 ${provider.api_key_env}`;
}

function optionalPositiveInteger(raw: string, label: string): number | undefined {
  const value = raw.trim();
  if (!value) return undefined;
  if (!/^\d+$/.test(value) || Number(value) < 1) throw new Error(`${label}必须是正整数`);
  return Number(value);
}

export function buildInitialModelParams(contextWindow: string, maxTokens: string): Record<string, number> {
  const params: Record<string, number> = {};
  const context = optionalPositiveInteger(contextWindow, "上下文窗口");
  const output = optionalPositiveInteger(maxTokens, "最大输出 Tokens");
  if (context !== undefined) params.context_window = context;
  if (output !== undefined) params.max_tokens = output;
  return params;
}
