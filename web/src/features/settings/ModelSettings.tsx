/** 设置面板的模型页: 供应商, 模型, 网关运行参数, 以及恢复点那一行。
 *
 * 表单该有哪些行全部来自后端的字段声明, 这里不维护一份会漂移的字段表。
 */

import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { CheckIcon, ChevronIcon } from "@/shared/ui/icons";
import {
  DEFAULT_TEMPERATURE,
  DEFAULT_TOP_P,
  buildInitialModelParams,
  modelPlaceholder,
  modelRef,
  providerAvailabilityCopy,
  splitModelRef,
} from "@/shared/lib/modelParams";
import { thinkingEffortList, thinkingEffortText } from "@/shared/lib/modelParams";
import type { AdminActions, AdminCatalog, Checkpoint, FieldSpec, KnownProvider, LlmRuntimeSettings, Model, Provider, ProviderProtocol } from "@/types/admin";

export type ModelSettingsView = "models" | "providers" | "gateway";

export type ModelSettingsProps = {
  /** 打开时停在哪个子页。只在挂载时读一次, 之后由子导航自己管。 */
  initialView?: ModelSettingsView;
  providers: Provider[];
  providerSettings: Provider[];
  providerFields: FieldSpec[];
  modelFields: FieldSpec[];
  knownProviders: KnownProvider[];
  llmRuntime: LlmRuntimeSettings | null;
  catalog: AdminCatalog;
  actions: AdminActions;
  onAddModel: (providerId: string, modelId: string, params: Record<string, unknown>) => Promise<boolean>;
  providerProtocols: ProviderProtocol[];
  onAddProvider: (body: {
    provider_id: string;
    name: string;
    api_base: string;
    protocol: string;
    api_key_env: string;
  }) => Promise<boolean>;
  onRemoveModel: (providerId: string, modelId: string) => void;
  onSaveModel: (providerId: string, modelId: string, changed: Record<string, string>) => void;
  onSaveProvider: (providerId: string, changed: Record<string, string>) => void;
  onSaveLlmRuntime: (changed: Record<string, string>) => void;
};

export function ProviderMark({ providerId, label }: { providerId: string; label?: string }) {
  return (
    <span className={`provider-mark provider-${providerId}`}>
      {(label || providerId).slice(0, 1).toUpperCase()}
    </span>
  );
}

export function ModelSettings({
  initialView = "models",
  providers,
  providerSettings,
  providerFields,
  modelFields,
  knownProviders,
  providerProtocols,
  llmRuntime,
  catalog,
  actions,
  onAddModel,
  onAddProvider,
  onRemoveModel,
  onSaveModel,
  onSaveProvider,
  onSaveLlmRuntime,
}: ModelSettingsProps) {
  const [view, setView] = useState<ModelSettingsView>(initialView);
  const [showAdd, setShowAdd] = useState(false);
  const [showAddProvider, setShowAddProvider] = useState(false);
  // 配置里有、但不在内置注册表里的那几家 = 用户自己加的。
  // knownProviders 现在同时包含内置与自建的, 由后端的 builtin 标记分开 —— 前端手抄
  // 一份内置清单的话, 加一家内置供应商就会让它出现在"自建"那一组下, 而不会有任何东西报错。
  const builtinProviders = knownProviders.filter((item) => item.builtin !== false);
  const customProviders = knownProviders.filter((item) => item.builtin === false);
  const configured = useMemo(
    () =>
      providers.flatMap((provider) =>
        provider.models.map((model) => ({ provider, model, ref: modelRef(provider.id, model.id) })),
      ),
    [providers],
  );
  const configuredRefs = configured.map((item) => item.ref);
  const [currentProviderId, currentModelId] = splitModelRef(catalog.currentModel);
  const current = configured.find((item) => item.ref === catalog.currentModel);
  const currentProvider = knownProviders.find((item) => item.id === currentProviderId);
  const overrideCount = Object.keys(catalog.overrides).length;

  return (
    <div className="model-settings">
      <div className="model-subnav" role="tablist" aria-label="模型设置分类">
        <button
          type="button"
          role="tab"
          aria-selected={view === "models"}
          className={view === "models" ? "active" : ""}
          onClick={() => setView("models")}
        >
          模型
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === "providers"}
          className={view === "providers" ? "active" : ""}
          onClick={() => setView("providers")}
        >
          供应商
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === "gateway"}
          className={view === "gateway" ? "active" : ""}
          onClick={() => setView("gateway")}
        >
          网关
        </button>
      </div>

      {view === "models" && (
        <>
          <div className="model-page-heading">
            <div>
              <h3>模型</h3>
              <p>管理已接入的模型，并指定 Forge 默认使用的模型。</p>
            </div>
            <button type="button" className="primary-action" onClick={() => setShowAdd(true)}>
              ＋ 添加模型
            </button>
          </div>

          <section className={`current-model-card ${current ? "configured" : ""}`}>
            <ProviderMark providerId={currentProviderId || "none"} label={currentProvider?.label} />
            <div>
              <span>当前默认模型</span>
              <strong>{currentModelId || "尚未设置"}</strong>
              <small>
                {current
                  ? `${current.provider.name} · 所有未单独指定的任务默认使用`
                  : "添加模型后即可设为默认模型"}
              </small>
            </div>
            {configuredRefs.length > 0 && (
              <select
                aria-label="更换默认模型"
                value={catalog.currentModel}
                onChange={(event) => {
                  const [providerId, modelId] = splitModelRef(event.target.value);
                  void actions.onSetCurrentModel(providerId, modelId);
                }}
              >
                <option value="" disabled>
                  选择模型
                </option>
                {configuredRefs.map((ref) => (
                  <option key={ref} value={ref}>
                    {ref}
                  </option>
                ))}
              </select>
            )}
          </section>

          <section className="configured-models">
            <div className="model-section-heading">
              <div>
                <h4>
                  已添加模型 <span>{configured.length}</span>
                </h4>
                <p>展开模型可编辑参数；修改后统一保存。</p>
              </div>
              <button type="button" onClick={() => setView("providers")}>
                管理供应商 →
              </button>
            </div>
            {configured.length ? (
              <div className="configured-model-list">
                {configured.map(({ provider, model, ref }) => (
                  <ModelEditor
                    key={ref}
                    provider={provider}
                    model={model}
                    fields={modelFields}
                    isCurrent={ref === catalog.currentModel}
                    onSave={onSaveModel}
                    onRemove={onRemoveModel}
                  />
                ))}
              </div>
            ) : (
              <div className="model-empty-state">
                <ProviderMark providerId="none" label="＋" />
                <strong>还没有模型</strong>
                <p>添加第一个模型后，Forge 才能开始对话和执行任务。</p>
                <button type="button" className="primary-action" onClick={() => setShowAdd(true)}>
                  添加模型
                </button>
              </div>
            )}
          </section>

          <details className="model-routing">
            <summary>
              <span>
                <strong>高级路由</strong>
                <small>Thinking 与按任务用途覆盖默认模型</small>
              </span>
              <span>
                {overrideCount ? `${overrideCount} 项已配置` : "使用默认模型"} <ChevronIcon />
              </span>
            </summary>
            <div className="model-routing-body">
              <h4>用途模型覆盖</h4>
              <p className="field-help">未设置的用途自动使用当前默认模型。</p>
              {catalog.origins.map((origin) => (
                <div className="admin-row" key={origin}>
                  <div>
                    <strong>{origin}</strong>
                    <small>{catalog.overrides[origin] || "使用默认模型"}</small>
                  </div>
                  <div className="row-actions">
                    <select
                      value={catalog.overrides[origin] ?? ""}
                      onChange={(event) => {
                        const [providerId, modelId] = splitModelRef(event.target.value);
                        actions.onSetOverride(origin, providerId, modelId);
                      }}
                    >
                      <option value="" disabled>
                        选择覆盖模型
                      </option>
                      {configuredRefs.map((ref) => (
                        <option key={ref} value={ref}>
                          {ref}
                        </option>
                      ))}
                    </select>
                    <button
                      onClick={() => actions.onClearOverride(origin)}
                      disabled={!catalog.overrides[origin]}
                    >
                      清除
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </details>
        </>
      )}

      {view === "providers" && (
        <>
          <div className="model-page-heading">
            <div>
              <h3>供应商</h3>
              <p>配置兼容端点与密钥环境变量；API 密钥不会在页面中读取或保存。</p>
            </div>
            <button type="button" className="primary-action" onClick={() => setShowAddProvider(true)}>
              ＋ 添加供应商
            </button>
          </div>
          <div className="provider-availability-list">
            {builtinProviders.map((known) => {
              const provider = providerSettings.find((item) => item.id === known.id);
              if (!provider) return null;
              return (
                <ProviderEditor
                  key={known.id}
                  provider={provider}
                  known={known}
                  fields={providerFields}
                  onSaveProvider={onSaveProvider}
                />
              );
            })}
          </div>
          {/* 自建的单独一组: 它们没有注册表默认值可以回落, 而且用户需要一眼看出哪几家是自己加的。
          可用性一律从 knownProviders 里取 —— 这里曾经自己编过一个 available: true,
          于是一家根本没配环境变量的供应商在界面上显示"密钥已就绪"。 */}
          {customProviders.length > 0 && (
            <>
              <h4 className="provider-group-heading">自建供应商</h4>
              <div className="provider-availability-list">
                {customProviders.map((known) => {
                  const provider = providerSettings.find((item) => item.id === known.id);
                  if (!provider) return null;
                  return (
                    <ProviderEditor
                      key={known.id}
                      provider={provider}
                      known={known}
                      fields={providerFields}
                      onSaveProvider={onSaveProvider}
                    />
                  );
                })}
              </div>
            </>
          )}
          <p className="field-help provider-protocol-help">
            目前只有 OpenAI Compatible <code>/chat/completions</code> 协议有对应的适配器；Ollama、vLLM
            等本地端点属于这一类。
          </p>
        </>
      )}

      {view === "gateway" && (
        <>
          <div className="model-page-heading">
            <div>
              <h3>网关治理</h3>
              <p>控制缓存、重试与故障熔断；通常保持默认值即可。</p>
            </div>
          </div>
          {llmRuntime && (
            <DraftForm
              fields={[
                {
                  key: "cache.enabled",
                  label: "缓存开关",
                  value: String(llmRuntime.cache.enabled),
                  choices: ["false", "true"],
                },
                {
                  key: "cache.ttl_seconds",
                  label: "缓存 TTL（秒）",
                  value: String(llmRuntime.cache.ttl_seconds ?? ""),
                },
                {
                  key: "cache.max_entries",
                  label: "缓存条目上限",
                  value: String(llmRuntime.cache.max_entries),
                },
                { key: "cache.origins", label: "缓存 origins", value: llmRuntime.cache.origins.join(",") },
                {
                  key: "circuit_breaker.enabled",
                  label: "熔断开关",
                  value: String(llmRuntime.circuit_breaker.enabled),
                  choices: ["false", "true"],
                },
                {
                  key: "circuit_breaker.failure_threshold",
                  label: "失败阈值",
                  value: String(llmRuntime.circuit_breaker.failure_threshold),
                },
                {
                  key: "circuit_breaker.cooldown_seconds",
                  label: "冷却时间（秒）",
                  value: String(llmRuntime.circuit_breaker.cooldown_seconds),
                },
                {
                  key: "retry.wait_threshold_seconds",
                  label: "429 等待阈值（秒）",
                  value: String(llmRuntime.retry.wait_threshold_seconds),
                },
              ]}
              onSave={onSaveLlmRuntime}
            />
          )}
        </>
      )}

      {showAddProvider && (
        <AddProviderDrawer
          protocols={providerProtocols}
          onClose={() => setShowAddProvider(false)}
          onAdd={onAddProvider}
        />
      )}

      {showAdd && (
        <AddModelDrawer
          knownProviders={knownProviders}
          providerSettings={providerSettings}
          onClose={() => setShowAdd(false)}
          onEditProvider={() => {
            setShowAdd(false);
            setView("providers");
          }}
          onAdd={onAddModel}
          onSetCurrent={actions.onSetCurrentModel}
        />
      )}
    </div>
  );
}

export function AddProviderDrawer({
  protocols,
  onClose,
  onAdd,
}: {
  protocols: ProviderProtocol[];
  onClose: () => void;
  onAdd: (body: {
    provider_id: string;
    name: string;
    api_base: string;
    protocol: string;
    api_key_env: string;
  }) => Promise<boolean>;
}) {
  const supported = protocols.find((item) => item.supported)?.value ?? "openai_compatible";
  const [providerId, setProviderId] = useState("");
  const [name, setName] = useState("");
  const [apiBase, setApiBase] = useState("");
  const [apiKeyEnv, setApiKeyEnv] = useState("");
  const [protocol, setProtocol] = useState(supported);
  const [saving, setSaving] = useState(false);
  const ready = providerId.trim().length > 0 && apiBase.trim().length > 0;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!ready || saving) return;
    setSaving(true);
    const done = await onAdd({
      provider_id: providerId.trim(),
      name: name.trim(),
      api_base: apiBase.trim(),
      protocol,
      api_key_env: apiKeyEnv.trim(),
    });
    setSaving(false);
    if (done) onClose();
  }

  return (
    <div
      className="model-drawer-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <aside className="model-drawer" role="dialog" aria-modal="true" aria-label="添加供应商">
        <header>
          <div>
            <h3>添加供应商</h3>
            <p>端点讲 OpenAI 兼容协议就能直接接入，不需要改代码。</p>
          </div>
          <button type="button" aria-label="关闭添加供应商" onClick={onClose}>
            ×
          </button>
        </header>
        <form onSubmit={submit}>
          <fieldset>
            <legend>1. 协议</legend>
            {/* 不支持的也列出来并禁用: 看不到这一项会让人以为 Forge 不打算支持, 于是去找别的工具。 */}
            <label className="model-input">
              <span>端点协议</span>
              <select value={protocol} onChange={(event) => setProtocol(event.target.value)}>
                {protocols.map((item) => (
                  <option key={item.value} value={item.value} disabled={!item.supported}>
                    {item.label}
                    {item.supported ? "" : "（暂不支持）"}
                  </option>
                ))}
              </select>
              <small>目前只有 OpenAI Compatible 有对应的适配器，另外两种还没有。</small>
            </label>
          </fieldset>

          <fieldset>
            <legend>2. 连接信息</legend>
            <label className="model-input">
              <span>供应商 ID</span>
              <input
                autoFocus
                value={providerId}
                onChange={(event) => setProviderId(event.target.value)}
                placeholder="acme"
              />
              <small>小写标识符，配置文件与模型引用都用它。</small>
            </label>
            <label className="model-input">
              <span>展示名</span>
              <input value={name} onChange={(event) => setName(event.target.value)} placeholder="Acme" />
              <small>留空则用 ID。</small>
            </label>
            <label className="model-input">
              <span>API 地址</span>
              <input
                value={apiBase}
                onChange={(event) => setApiBase(event.target.value)}
                placeholder="https://acme.example.com/v1/chat/completions"
              />
              <small>完整的 /chat/completions 端点。</small>
            </label>
            <label className="model-input">
              <span>API Key 环境变量</span>
              <input
                value={apiKeyEnv}
                onChange={(event) => setApiKeyEnv(event.target.value)}
                placeholder="ACME_API_KEY"
              />
              <small>Forge 只读环境变量名，不保存密钥本身。免密钥的本地端点可留空。</small>
            </label>
          </fieldset>

          <footer>
            <button type="button" onClick={onClose}>
              取消
            </button>
            <button type="submit" className="primary-action" disabled={!ready || saving}>
              {saving ? "添加中…" : "添加供应商"}
            </button>
          </footer>
        </form>
      </aside>
    </div>
  );
}

export function AddModelDrawer({
  knownProviders,
  providerSettings,
  onClose,
  onEditProvider,
  onAdd,
  onSetCurrent,
}: {
  knownProviders: KnownProvider[];
  providerSettings: Provider[];
  onClose: () => void;
  onEditProvider: () => void;
  onAdd: (providerId: string, modelId: string, params: Record<string, unknown>) => Promise<boolean>;
  onSetCurrent: (providerId: string, modelId: string) => Promise<boolean>;
}) {
  const [providerId, setProviderId] = useState(knownProviders[0]?.id ?? "");
  const [modelId, setModelId] = useState("");
  const [contextWindow, setContextWindow] = useState("");
  const [maxTokens, setMaxTokens] = useState("");
  // 采样与 thinking 在**添加时**就填: 它们是这个模型怎么用的一部分, 不是事后想起来
  // 才去某个单独入口调的东西。
  const [temperature, setTemperature] = useState(DEFAULT_TEMPERATURE);
  const [topP, setTopP] = useState(DEFAULT_TOP_P);
  const [thinkingMode, setThinkingMode] = useState("off");
  const [thinkingEffort, setThinkingEffort] = useState("");
  const [makeDefault, setMakeDefault] = useState(true);
  const [saving, setSaving] = useState(false);
  const [validationError, setValidationError] = useState("");
  const selected = knownProviders.find((item) => item.id === providerId);
  const selectedSettings = providerSettings.find((item) => item.id === providerId);

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopImmediatePropagation();
      onClose();
    };
    window.addEventListener("keydown", closeOnEscape, true);
    return () => window.removeEventListener("keydown", closeOnEscape, true);
  }, [onClose]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!providerId || !modelId.trim() || saving) return;
    let params: Record<string, unknown>;
    try {
      params = buildInitialModelParams(contextWindow, maxTokens, {
        temperature,
        topP,
        thinkingMode,
        thinkingEffort,
      });
      setValidationError("");
    } catch (reason) {
      setValidationError((reason as Error).message);
      return;
    }
    setSaving(true);
    const created = await onAdd(providerId, modelId.trim(), params);
    if (created && makeDefault) await onSetCurrent(providerId, modelId.trim());
    setSaving(false);
    if (created) onClose();
  }

  return (
    <div
      className="model-drawer-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <aside className="model-drawer" role="dialog" aria-modal="true" aria-label="添加模型">
        <header>
          <div>
            <h3>添加模型</h3>
            <p>先选择供应商，再填写模型 ID。</p>
          </div>
          <button type="button" aria-label="关闭添加模型" onClick={onClose}>
            ×
          </button>
        </header>
        <form onSubmit={submit}>
          <fieldset>
            <legend>1. 选择供应商</legend>
            <div className="provider-choice-grid">
              {knownProviders.map((provider) => (
                <button
                  type="button"
                  key={provider.id}
                  className={provider.id === providerId ? "active" : ""}
                  aria-pressed={provider.id === providerId}
                  onClick={() => {
                    setProviderId(provider.id);
                    setValidationError("");
                  }}
                >
                  <ProviderMark providerId={provider.id} label={provider.label} />
                  <span>
                    <strong>{provider.label}</strong>
                    <small className={provider.available || !provider.api_key_env ? "ready" : ""}>
                      {providerAvailabilityCopy(provider)}
                    </small>
                  </span>
                  <CheckIcon />
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset>
            <legend>2. 模型信息</legend>
            <label className="model-input">
              <span>模型 ID</span>
              <input
                autoFocus
                value={modelId}
                onChange={(event) => setModelId(event.target.value)}
                placeholder={modelPlaceholder(providerId)}
              />
              <small>填写供应商 API 使用的准确模型名称。</small>
            </label>
            <div
              className={`provider-connection-summary ${selected?.available || !selected?.api_key_env ? "ready" : "warning"}`}
            >
              <span>
                <i /> <strong>{selected?.label || "供应商"}</strong> ·{" "}
                {providerAvailabilityCopy(selected ?? { available: false })}
                {selectedSettings?.api_base ? ` · ${selectedSettings.api_base}` : ""}
              </span>
              <button type="button" onClick={onEditProvider}>
                编辑连接配置
              </button>
            </div>
          </fieldset>

          <fieldset>
            <legend>3. 思考</legend>
            <label className="model-input">
              <span>Thinking</span>
              <select value={thinkingMode} onChange={(event) => setThinkingMode(event.target.value)}>
                <option value="off">关闭</option>
                <option value="on">开启</option>
              </select>
              <small>开启后模型会先推理再回答，更准也更慢更贵。</small>
            </label>
            {thinkingMode === "on" && (
              <label className="model-input">
                <span>思考强度</span>
                <input
                  value={thinkingEffort}
                  onChange={(event) => setThinkingEffort(event.target.value)}
                  placeholder="留空用模型默认"
                />
                <small>填模型自己声明的强度名，例如 low / medium / high。</small>
              </label>
            )}
          </fieldset>

          <details className="model-add-advanced">
            <summary>
              高级参数{" "}
              <span>
                添加后也可以修改 <ChevronIcon />
              </span>
            </summary>
            <div>
              <label>
                <span>温度</span>
                <input
                  inputMode="decimal"
                  value={temperature}
                  onChange={(event) => setTemperature(event.target.value)}
                />
              </label>
              <label>
                <span>top_p</span>
                <input inputMode="decimal" value={topP} onChange={(event) => setTopP(event.target.value)} />
              </label>
              <label>
                <span>上下文窗口</span>
                <input
                  inputMode="numeric"
                  value={contextWindow}
                  onChange={(event) => setContextWindow(event.target.value)}
                  placeholder="使用供应商默认值"
                />
              </label>
              <label>
                <span>最大输出 Tokens</span>
                <input
                  inputMode="numeric"
                  value={maxTokens}
                  onChange={(event) => setMaxTokens(event.target.value)}
                  placeholder="使用供应商默认值"
                />
              </label>
            </div>
          </details>
          {validationError && (
            <p className="model-validation-error" role="alert">
              {validationError}
            </p>
          )}
          <label className="model-default-check">
            <input
              type="checkbox"
              checked={makeDefault}
              onChange={(event) => setMakeDefault(event.target.checked)}
            />
            <span>添加后设为默认模型</span>
          </label>
          <footer>
            <button type="button" onClick={onClose}>
              取消
            </button>
            <button
              type="submit"
              className="primary-action"
              disabled={!providerId || !modelId.trim() || saving}
            >
              {saving ? "添加中…" : makeDefault ? "添加并使用" : "添加模型"}
            </button>
          </footer>
        </form>
      </aside>
    </div>
  );
}

export function CheckpointRow({
  checkpoint,
  onRestore,
  onPreview,
}: {
  checkpoint: Checkpoint;
  onRestore: (id: string) => void;
  onPreview: (id: string) => Promise<string>;
}) {
  const [preview, setPreview] = useState("");
  return (
    <div className="admin-row checkpoint-row">
      <div>
        <strong>{checkpoint.checkpoint_id}</strong>
        <small>
          {checkpoint.status} · {checkpoint.snapshot_strategy} · {checkpoint.created_at}
        </small>
        {preview && <pre className="checkpoint-preview">{preview}</pre>}
      </div>
      <div className="row-actions">
        <button
          onClick={() => {
            if (preview) {
              setPreview("");
              return;
            }
            onPreview(checkpoint.checkpoint_id).then(setPreview);
          }}
        >
          {preview ? "收起" : "预览"}
        </button>
        <button onClick={() => onRestore(checkpoint.checkpoint_id)}>恢复</button>
      </div>
    </div>
  );
}

export function ProviderEditor({
  provider,
  known,
  fields,
  onSaveProvider,
}: {
  provider: Provider;
  known: KnownProvider;
  fields: FieldSpec[];
  onSaveProvider: (providerId: string, changed: Record<string, string>) => void;
}) {
  // 表单行由后端给（从 ProviderConfig 的字段声明派生）。这里曾经硬编码过一份同样的
  // (字段名, 中文标签) 数组——加一个字段时它不会报错，只会在页面上少一行。
  const rows = fields.map((field) => ({
    key: field.name,
    label: field.label,
    value: String((provider as unknown as Record<string, unknown>)[field.name] ?? ""),
  }));
  const availability = providerAvailabilityCopy(known);
  return (
    <details className="provider-editor">
      <summary className="provider-heading">
        <ProviderMark providerId={provider.id} label={provider.name} />
        <span>
          <strong>{provider.name}</strong>
          <small>{provider.api_base || "尚未配置 API 端点"}</small>
        </span>
        <em className={known.available || !known.api_key_env ? "ready" : ""}>{availability}</em>
        <ChevronIcon />
      </summary>
      <DraftForm
        className="provider-fields"
        fields={rows}
        onSave={(changed) => onSaveProvider(provider.id, changed)}
      />
    </details>
  );
}

export function ModelEditor({
  provider,
  model,
  fields,
  isCurrent,
  onSave,
  onRemove,
}: {
  provider: Provider;
  model: Model;
  fields: FieldSpec[];
  isCurrent: boolean;
  onSave: (providerId: string, modelId: string, changed: Record<string, string>) => void;
  onRemove: (providerId: string, modelId: string) => void;
}) {
  const [confirmRemove, setConfirmRemove] = useState(false);
  return (
    <details className="model-editor">
      <summary>
        <ProviderMark providerId={provider.id} label={provider.name} />
        <span>
          <strong>
            {model.id}
            {isCurrent && <em>默认</em>}
          </strong>
          <small>
            {provider.name} ·{" "}
            {model.params.context_window ? `${model.params.context_window} 上下文` : "使用供应商默认参数"}
          </small>
        </span>
        <span className="model-row-status">
          <i /> 可用
        </span>
        <ChevronIcon />
      </summary>
      <DraftForm
        className="model-fields"
        fields={[
          {
            key: "thinking_mode",
            label: "Thinking",
            value: model.params.thinking_mode ?? "off",
            choices: ["off", "on"],
          },
          ...fields.map((field) => ({
            key: field.name,
            label: field.label,
            value: String(model.params[field.name] ?? ""),
          })),
          {
            key: "thinking_effort",
            label: "当前 Thinking 强度",
            value: thinkingEffortText(model.params.thinking_effort),
          },
          {
            key: "thinking_efforts",
            label: "Thinking 强度（逗号分隔）",
            value: thinkingEffortList(model.params.thinking_efforts).join(","),
          },
          {
            key: "thinking_default_effort",
            label: "默认 Thinking 强度",
            value: thinkingEffortText(model.params.thinking_default_effort),
          },
          { key: "extra", label: "厂商扩展 JSON", value: JSON.stringify(model.params.extra ?? {}) },
        ]}
        onSave={(changed) => onSave(provider.id, model.id, changed)}
      />
      <div className="model-remove-row">
        <span>{confirmRemove ? "再次点击将移除此模型" : "移除后不会删除供应商连接配置"}</span>
        <button
          type="button"
          className={confirmRemove ? "confirm" : ""}
          onClick={() => {
            if (!confirmRemove) {
              setConfirmRemove(true);
              return;
            }
            onRemove(provider.id, model.id);
          }}
        >
          {confirmRemove ? "确认移除" : "移除模型"}
        </button>
        {confirmRemove && (
          <button type="button" onClick={() => setConfirmRemove(false)}>
            取消
          </button>
        )}
      </div>
    </details>
  );
}

export type DraftField = { key: string; label: string; value: string; choices?: string[] };

/**
 * 一组字段一起改、一次保存。
 *
 * 逐项保存的问题不只是点击次数: 每次保存都要重新拉一遍配置, 而重新拉配置会把同一张表单里
 * 其他还没保存的输入冲掉 —— 用户填了三格, 保存第一格, 另外两格就没了。
 */
export function DraftForm({
  fields,
  onSave,
  className = "runtime-settings",
}: {
  fields: DraftField[];
  onSave: (changed: Record<string, string>) => void;
  className?: string;
}) {
  const committed = useMemo(
    () => Object.fromEntries(fields.map((field) => [field.key, field.value])),
    [fields],
  );
  const signature = fields.map((field) => `${field.key}=${field.value}`).join("\u0001");
  const [draft, setDraft] = useState<Record<string, string>>(committed);
  // 服务端的值变了 (保存成功, 或别处改动后刷新) 才重置草稿, 不在每次渲染时覆盖输入。
  useEffect(() => {
    setDraft(
      Object.fromEntries(
        signature.split("\u0001").map((pair) => {
          const at = pair.indexOf("=");
          return [pair.slice(0, at), pair.slice(at + 1)];
        }),
      ),
    );
  }, [signature]);
  const changed = Object.fromEntries(
    Object.entries(draft).filter(([key, value]) => committed[key] !== value),
  );
  const dirty = Object.keys(changed).length;
  return (
    <div className={`draft-form ${className}`}>
      {fields.map((field) => (
        <label
          className={`editable-value ${draft[field.key] !== committed[field.key] ? "dirty" : ""}`}
          key={field.key}
        >
          <span>{field.label}</span>
          {field.choices ? (
            <select
              value={draft[field.key] ?? ""}
              onChange={(event) => setDraft((state) => ({ ...state, [field.key]: event.target.value }))}
            >
              {field.choices.map((choice) => (
                <option key={choice} value={choice}>
                  {choice}
                </option>
              ))}
            </select>
          ) : (
            <input
              value={draft[field.key] ?? ""}
              onChange={(event) => setDraft((state) => ({ ...state, [field.key]: event.target.value }))}
            />
          )}
        </label>
      ))}
      <div className="draft-actions">
        <span>{dirty ? `${dirty} 项待保存` : "没有改动"}</span>
        <button onClick={() => setDraft(committed)} disabled={!dirty}>
          撤销
        </button>
        <button className="primary" onClick={() => onSave(changed)} disabled={!dirty}>
          保存
        </button>
      </div>
    </div>
  );
}
