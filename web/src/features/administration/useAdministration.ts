/** 管理面: 模型, 供应商, 网关运行参数, 工具清单, 工作区根, 学习规则, 恢复点与状态。
 *
 * 这一整块从 `App` 里搬出来 (ADR-0048 决策 5)。它有十七个状态字段和十几个动作, 而
 * 会话正文一个都用不到 —— 混在一起时, 改一个模型下拉框要在同一个函数里翻过整条时间线
 * 的逻辑, 反过来也一样。
 *
 * 全部写操作都收敛到"改完重新拉一次": 后端改配置会顺带重载 LLM 运行时, 所以本地
 * 猜一个新状态迟早会和后端分叉。
 */

import { useCallback, useState } from "react";
import { api } from "@/shared/api/client";
import { indexTools } from "@/shared/lib/run/tools";
import type { Checkpoint, FieldSpec, KnownProvider, LearnedRule, LlmRuntimeSettings, ModelsResponse, Provider, ProviderProtocol, RecoveryStatus, StatusView, ThinkingView, WorkspaceRoot } from "@/types/admin";
import type { ToolSpec, ToolsResponse } from "@/types/tools";
import type { ToolDirectory } from "@/shared/lib/run/tools";

export type ProviderDraft = {
  provider_id: string;
  name: string;
  api_base: string;
  protocol: string;
  api_key_env: string;
};

export function useAdministration(onError: (message: string) => void, reloadProjects: () => Promise<void>) {
  const [workspaceRoots, setWorkspaceRoots] = useState<WorkspaceRoot[]>([]);
  const [rules, setRules] = useState<LearnedRule[]>([]);
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [knownProviders, setKnownProviders] = useState<KnownProvider[]>([]);
  const [providerProtocols, setProviderProtocols] = useState<ProviderProtocol[]>([]);
  const [providerSettings, setProviderSettings] = useState<Provider[]>([]);
  const [providerFields, setProviderFields] = useState<FieldSpec[]>([]);
  const [modelFields, setModelFields] = useState<FieldSpec[]>([]);
  const [llmRuntime, setLlmRuntime] = useState<LlmRuntimeSettings | null>(null);
  const [currentModel, setCurrentModel] = useState("");
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [origins, setOrigins] = useState<string[]>([]);
  const [thinking, setThinking] = useState<ThinkingView>({ model: "", configured: false });
  const [tools, setTools] = useState<ToolSpec[]>([]);
  // 运行过程视图查它把工具名翻成中文. 与上面那份分开: 那份跟着模式走 (管理面板要的
  // 就是这个语义), 而历史事件里的调用可能发生在换模式之前.
  const [toolDirectory, setToolDirectory] = useState<ToolDirectory>({});
  const [statusView, setStatusView] = useState<StatusView | null>(null);
  const [recovery, setRecovery] = useState<RecoveryStatus | null>(null);

  const fail = useCallback((reason: unknown) => onError((reason as Error).message), [onError]);

  const loadModels = useCallback(async () => {
    const models = await api<ModelsResponse>("/models");
    setProviders(models.items);
    setProviderSettings(models.provider_settings);
    setProviderFields(models.provider_fields);
    setModelFields(models.model_fields);
    setKnownProviders(models.known_providers);
    setProviderProtocols(models.provider_protocols ?? []);
    setLlmRuntime(models.runtime);
    setCurrentModel(models.current_model);
    setOverrides(models.overrides);
    setOrigins(models.origins);
    setThinking(models.thinking);
  }, []);

  /** 只要工具名的那一份。拉不回来不报错, 只是每一行显示工具名而不是中文 —— 那是
   *  toolActionLabel 的既定退路, 比为一份展示用的名字表弹一条错误提示合适。 */
  const loadToolDirectory = useCallback(async () => {
    try {
      const value = await api<ToolsResponse>("/tools");
      setToolDirectory(indexTools(value.all));
    } catch {
      /* 退回显示工具名 */
    }
  }, []);

  const loadAll = useCallback(async () => {
    const [roots, ruleList, checkpointList, toolList, status, recoveryState] = await Promise.all([
      api<{ items: WorkspaceRoot[] }>("/workspace-roots"),
      api<{ items: LearnedRule[] }>("/rules"),
      api<{ items: Checkpoint[] }>("/checkpoints"),
      api<ToolsResponse>("/tools"),
      api<StatusView>("/status"),
      api<RecoveryStatus>("/recovery"),
    ]);
    setWorkspaceRoots(roots.items);
    setRules(ruleList.items);
    setCheckpoints(checkpointList.items);
    setTools(toolList.items);
    setToolDirectory(indexTools(toolList.all));
    setStatusView(status);
    setRecovery(recoveryState);
    await loadModels();
  }, [loadModels]);

  /** 一次写操作 + 一次重新拉取。`after` 决定拉哪一份。 */
  const write = useCallback(
    async (run: () => Promise<unknown>, after: () => Promise<unknown> = loadAll): Promise<boolean> => {
      try {
        await run();
        await after();
        return true;
      } catch (reason) {
        fail(reason);
        return false;
      }
    },
    [fail, loadAll],
  );

  /** 逐字段 PATCH, 但只在整批做完后刷新一次: 中途刷新会把还没保存的输入冲掉。 */
  const saveFields = useCallback(
    async (changed: Record<string, string>, endpoint: (field: string) => string) => {
      try {
        for (const [field, value] of Object.entries(changed)) {
          await api(endpoint(field), { method: "PATCH", body: JSON.stringify({ value }) });
        }
      } catch (reason) {
        fail(reason);
      }
      await loadModels().catch(() => undefined);
    },
    [fail, loadModels],
  );

  const withProjects = useCallback(async () => {
    await Promise.all([reloadProjects(), loadAll()]);
  }, [reloadProjects, loadAll]);

  return {
    workspaceRoots,
    rules,
    checkpoints,
    providers,
    knownProviders,
    providerProtocols,
    providerSettings,
    providerFields,
    modelFields,
    llmRuntime,
    currentModel,
    overrides,
    origins,
    thinking,
    tools,
    toolDirectory,
    statusView,
    recovery,

    loadAll,
    loadModels,
    loadToolDirectory,

    addWorkspace: (path: string, access: string) =>
      write(
        () => api("/workspace-roots", { method: "POST", body: JSON.stringify({ path, access }) }),
        withProjects,
      ),
    removeWorkspace: (path: string) =>
      write(
        () => api(`/workspace-roots?path=${encodeURIComponent(path)}`, { method: "DELETE" }),
        withProjects,
      ),
    revokeRule: (id: string) => write(() => api(`/rules/${id}`, { method: "DELETE" })),
    pruneRules: () => write(() => api("/rules/prune", { method: "POST" })),
    restoreCheckpoint: (id: string) =>
      write(() =>
        api(`/checkpoints/${id}/restore`, {
          method: "POST",
          body: JSON.stringify({ force_conflicts: false }),
        }),
      ),
    undoLatest: () => write(() => api("/undo", { method: "POST" })),

    addProvider: (body: ProviderDraft) =>
      write(() => api("/providers", { method: "POST", body: JSON.stringify(body) })),
    addModel: (providerId: string, modelId: string, params: Record<string, unknown>) =>
      write(
        () =>
          api("/models", {
            method: "POST",
            body: JSON.stringify({ provider_id: providerId, model_id: modelId, params }),
          }),
        loadModels,
      ),
    removeModel: (providerId: string, modelId: string) =>
      write(
        () =>
          api(
            `/models?provider_id=${encodeURIComponent(providerId)}&model_id=${encodeURIComponent(modelId)}`,
            { method: "DELETE" },
          ),
        loadModels,
      ),
    chooseCurrentModel: (providerId: string, modelId: string) =>
      write(
        () =>
          api("/models/current", {
            method: "PUT",
            body: JSON.stringify({ provider_id: providerId, model_id: modelId }),
          }),
        loadModels,
      ),
    setModelOverride: (origin: string, providerId: string, modelId: string) =>
      write(
        () =>
          api(`/model-overrides/${encodeURIComponent(origin)}`, {
            method: "PUT",
            body: JSON.stringify({ provider_id: providerId, model_id: modelId }),
          }),
        loadModels,
      ),
    clearModelOverride: (origin: string) =>
      write(() => api(`/model-overrides/${encodeURIComponent(origin)}`, { method: "DELETE" }), loadModels),

    saveModelFields: (providerId: string, modelId: string, changed: Record<string, string>) =>
      saveFields(
        changed,
        (field) =>
          `/models/${field}?provider_id=${encodeURIComponent(providerId)}&model_id=${encodeURIComponent(modelId)}`,
      ),
    saveProviderFields: (providerId: string, changed: Record<string, string>) =>
      saveFields(changed, (field) => `/providers/${encodeURIComponent(providerId)}/${field}`),
    // key 形如 "cache.ttl_seconds": 网关配置分段, 但对用户是同一张表单。
    saveLlmRuntime: (changed: Record<string, string>) =>
      saveFields(changed, (key) => `/llm-runtime/${key.replace(".", "/")}`),

    updateThinking: async (mode: string, effort: string) => {
      try {
        const result = await api<{ thinking: ThinkingView }>("/thinking", {
          method: "POST",
          body: JSON.stringify({ mode, effort }),
        });
        setThinking(result.thinking);
      } catch (reason) {
        fail(reason);
      }
    },

    /** 预览把失败也当成内容返回: 它渲染在一个折叠区里, 弹错误横幅太重。 */
    previewCheckpoint: async (id: string): Promise<string> => {
      try {
        const result = await api<Record<string, unknown>>(`/checkpoints/${id}/preview`);
        return JSON.stringify(result, null, 2);
      } catch (reason) {
        return (reason as Error).message;
      }
    },
  };
}
