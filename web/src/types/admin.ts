/** 管理面的响应形状: 供应商, 模型, 网关, 工作区根, 学习规则, 恢复点与状态。 */

import type { ToolSpec } from "@/types/tools";

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

export type KnownProvider = {
  id: string;
  label: string;
  api_key_env?: string;
  available: boolean;
  builtin?: boolean;
};

export type ThinkingView = {
  model: string;
  configured: boolean;
  mode?: string;
  effort?: string;
  supported_efforts?: string[];
};

/** 三种协议全都列出来; 不支持的要看得见但选不了。 */
export type ProviderProtocol = { value: string; label: string; supported: boolean };

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
