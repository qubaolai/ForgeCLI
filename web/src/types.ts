/** 后端响应的形状。跨功能共享的那些放这里, 只服务一个功能的留在那个功能自己的模块里
 * (ADR-0048 决策 5)。
 *
 * 中文名, 说明, 来源与生效时机一律来自后端 SCHEMA, 前端不另写一份 —— 同一个开关在
 * 终端菜单和网页上叫不同名字时, 两边都不会报错, 只会让用户以为是两个开关。
 */

import type { ToolDirectory } from "./runModel";

export type Project = {
  project_id: string;
  primary_workspace_root: string;
  workspace_roots: string[];
};

export type Session = {
  session_id: string;
  title: string;
  updated_at: string;
  status: string;
  /** 两个轴, 见 Stance。历史会话可能没有这一项。 */
  mode?: { sandbox: string; approval: string };
};

export type TranscriptEvent = {
  event_id: string;
  type: string;
  created_at: string;
  payload: { role?: string; text?: string; status?: string; turn_id?: string };
};

export type Setting = {
  key: string;
  // 中文名与说明来自后端 SCHEMA，不在这里另写一份：同一个开关在终端菜单和这里叫不同
  // 名字时，两边都不会报错，只会让用户以为是两个开关。
  label: string;
  help: string;
  level: string;
  kind: string;
  value: string;
  choices: string[];
  // 来源与生效时机也来自 SCHEMA (ADR-0048 决策 6)。终端一直显示这两样并支持恢复默认,
  // 这里以前只拿得到有效值 —— 于是同一份声明在两个入口给出的能力不一样。
  default: string;
  overridden: boolean;
  effect: string;
};


export type Planning = {
  markdown?: string;
  plan?: {
    plan_id: string;
    title: string;
    goal: string;
    status: string;
    steps: Array<{ title: string; detail: string }>;
  };
  todo?: {
    items: Array<{ title: string; status: string }>;
  };
};

export type TurnRunState = {
  run_id: string;
  // 在途轮次的会话身份。run_id 标识"这次后台执行", 这一对标识"会话里的第几轮"。
  session_id?: string;
  turn_id?: string;
  status: string;
  response?: { turn_id: string; text: string };
  error?: string;
};

export type WorkspaceRoot = { path: string; access: string };
export type LearnedRule = {
  rule_id: string;
  label: string;
  scope: string;
  revoked: boolean;
  match: { mode: string };
};
export type Checkpoint = {
  checkpoint_id: string;
  status: string;
  snapshot_strategy: string;
  created_at: string;
  mutations: { entries?: unknown[] };
};
export type Model = {
  provider: string;
  id: string;
  params: Record<string, unknown> & {
    context_window?: number;
    max_tokens?: number;
    temperature?: number;
    top_p?: number;
    thinking_mode?: string;
    thinking_effort?: string;
    thinking_efforts?: string[];
    thinking_default_effort?: string;
  };
};
export type Provider = {
  id: string;
  name: string;
  api_base: string;
  api_key_env?: string;
  timeout: number;
  max_retries: number;
  models: Model[];
};
export type KnownProvider = { id: string; label: string; api_key_env?: string; available: boolean; builtin?: boolean };
export type ThinkingView = {
  model: string;
  configured: boolean;
  mode?: string;
  effort?: string;
  supported_efforts?: string[];
};

/**
 * Thinking 强度在新接口中是字符串；兼容旧服务端曾经返回的 `{value: string}`
 * 值对象，避免设置页把对象直接交给 input 后显示成 `[object Object]`。
 */
export function thinkingEffortText(value: unknown): string {
  if (typeof value === "string") return value;
  if (value && typeof value === "object" && "value" in value) {
    return thinkingEffortText((value as { value?: unknown }).value);
  }
  return "";
}

export function thinkingEffortList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map(thinkingEffortText).filter(Boolean);
}
export type ToolSpec = {
  name: string;
  title: string;
  description: string;
  declared_capabilities: string[];
  default_timeout_seconds: number;
};
/** `/tools` 的 `all`: 与模式无关的全量展示名, 见后端那个路由的说明。 */
export type ToolDirectoryItem = { name: string; title: string; capabilities: string[] };
/** 三种协议全都列出来; 不支持的要看得见但选不了。 */
export type ProviderProtocol = { value: string; label: string; supported: boolean };
export type ToolsResponse = { items: ToolSpec[]; all: ToolDirectoryItem[] };

/** 后端给的是数组 (顺序稳定, 便于诊断), 展示要的是按名字查 —— 转一次即可。 */
export function indexTools(items: ToolDirectoryItem[]): ToolDirectory {
  return Object.fromEntries(items.map((item) => [item.name, { title: item.title, capabilities: item.capabilities }]));
}
export type StatusView = {
  session_id: string;
  mode: string;
  last_event_id: string;
  workspace_roots: string[];
  model: string;
  busy: boolean;
};
export type RecoveryStatus = {
  checkpoint_count: number;
  pending: Array<{ checkpoint_id: string; status: string; created_at: string }>;
};
export type PlanIndexView = {
  active_plan_id: string;
  active_todo_id: string;
  plans: Array<{ plan_id: string; revision: number; status: string; title: string; updated_at: string }>;
};
/** 设置面板要展示的只读快照, 与它能触发的动作分开传, 免得再堆十几个平铺 prop。 */
export type AdminCatalog = {
  currentModel: string;
  overrides: Record<string, string>;
  origins: string[];
  thinking: ThinkingView;
  tools: ToolSpec[];
  status: StatusView | null;
  recovery: RecoveryStatus | null;
};
export type AdminActions = {
  onSetCurrentModel: (providerId: string, modelId: string) => Promise<boolean>;
  onSetOverride: (origin: string, providerId: string, modelId: string) => void;
  onClearOverride: (origin: string) => void;
  onPruneRules: () => void;
  onUndo: () => void;
  onPreviewCheckpoint: (id: string) => Promise<string>;
};
export type FieldSpec = { name: string; label: string; kind: string };
export type ModelsResponse = {
  items: Provider[];
  // 每家内置供应商各一条（没配过的带注册表默认值）：要能在添加第一个模型之前就把端点填好。
  provider_settings: Provider[];
  // 表单该有哪些行、每行叫什么——从后端的 ProviderConfig 字段声明派生。
  provider_fields: FieldSpec[];
  model_fields: FieldSpec[];
  known_providers: KnownProvider[];
  provider_protocols: ProviderProtocol[];
  runtime: LlmRuntimeSettings;
  current_model: string;
  overrides: Record<string, string>;
  origins: string[];
  thinking: ThinkingView;
};
export type LlmRuntimeSettings = {
  cache: { enabled: boolean; ttl_seconds?: number; max_entries: number; origins: string[] };
  circuit_breaker: { enabled: boolean; failure_threshold: number; cooldown_seconds: number };
  retry: { wait_threshold_seconds: number };
};
