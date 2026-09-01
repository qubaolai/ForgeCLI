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

/** 采样参数的出厂默认值: 添加模型时预填, 用户可改。 */
export const DEFAULT_TEMPERATURE = "0.7";
export const DEFAULT_TOP_P = "1.0";

export function buildInitialModelParams(
  contextWindow: string,
  maxTokens: string,
  sampling: { temperature: string; topP: string; thinkingMode: string; thinkingEffort: string } = {
    temperature: DEFAULT_TEMPERATURE, topP: DEFAULT_TOP_P, thinkingMode: "off", thinkingEffort: "",
  },
): Record<string, number | string> {
  const params: Record<string, number | string> = {};
  const context = optionalPositiveInteger(contextWindow, "上下文窗口");
  const output = optionalPositiveInteger(maxTokens, "最大输出 Tokens");
  if (context !== undefined) params.context_window = context;
  if (output !== undefined) params.max_tokens = output;
  // 温度与 top_p 一律带上: 留空等于让每家供应商用自己的默认值, 而那些默认值互不相同
  // —— 同一个提示词换个供应商就得到不同的发散程度, 而配置里看不出为什么。
  params.temperature = boundedFloat(sampling.temperature, "温度", 0, 2);
  params.top_p = boundedFloat(sampling.topP, "top_p", 0, 1);
  if (sampling.thinkingMode) params.thinking_mode = sampling.thinkingMode;
  if (sampling.thinkingEffort.trim()) params.thinking_effort = sampling.thinkingEffort.trim();
  return params;
}

function boundedFloat(raw: string, label: string, low: number, high: number): number {
  const value = Number(raw);
  if (!Number.isFinite(value) || value < low || value > high) {
    throw new Error(`${label}必须是 ${low} 到 ${high} 之间的数字`);
  }
  return value;
}
