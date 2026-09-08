/** 设置面板外壳与常规页。
 *
 * 每一项的来源, 默认值与生效时机都由后端配置视图给出 —— 终端读的是同一份
 * (ADR-0048 决策 6)。
 */

import { useEffect, useState } from "react";
import type {
  AdminActions,
  AdminCatalog,
  Checkpoint,
  FieldSpec,
  KnownProvider,
  LearnedRule,
  LlmRuntimeSettings,
  Provider,
  ProviderProtocol,
  Setting,
  WorkspaceRoot,
} from "../../types";
import { CheckpointRow, ModelSettings } from "./ModelSettings";

export type SettingsTab = "general" | "models" | "security" | "recovery" | "status";

export type SettingsPanelProps = {
  /** 打开时停在哪个 tab。只在挂载时读一次, 之后由面板自己管。 */
  initialTab?: SettingsTab;
  items: Setting[];
  roots: WorkspaceRoot[];
  rules: LearnedRule[];
  checkpoints: Checkpoint[];
  providers: Provider[];
  providerSettings: Provider[];
  providerFields: FieldSpec[];
  modelFields: FieldSpec[];
  knownProviders: KnownProvider[];
  llmRuntime: LlmRuntimeSettings | null;
  catalog: AdminCatalog;
  actions: AdminActions;
  onClose: () => void;
  onSave: (item: Setting, value: string) => void;
  onReset: (item: Setting) => void;
  onAddRoot: (path: string, access: string) => void;
  onRemoveRoot: (path: string) => void;
  onRevokeRule: (id: string) => void;
  onRestore: (id: string) => void;
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

export function SettingsPanel({
  initialTab = "general",
  items,
  roots,
  rules,
  checkpoints,
  providers,
  providerSettings,
  providerFields,
  modelFields,
  knownProviders,
  providerProtocols,
  llmRuntime,
  catalog,
  actions,
  onClose,
  onSave,
  onReset,
  onAddRoot,
  onRemoveRoot,
  onRevokeRule,
  onRestore,
  onAddModel,
  onAddProvider,
  onRemoveModel,
  onSaveModel,
  onSaveProvider,
  onSaveLlmRuntime,
}: SettingsPanelProps) {
  const [tab, setTab] = useState<SettingsTab>(initialTab);
  const [path, setPath] = useState("");
  const [access, setAccess] = useState("read");
  return (
    <div
      className="modal-backdrop settings-layer"
      role="dialog"
      aria-modal="true"
      aria-label="设置"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section className="settings-panel">
        <header>
          <div>
            <h2>设置</h2>
            <p>应用级与当前项目配置</p>
          </div>
          <button onClick={onClose}>×</button>
        </header>
        <div className="settings-body">
          <nav>
            <button className={tab === "general" ? "active" : ""} onClick={() => setTab("general")}>
              常规
            </button>
            <button className={tab === "models" ? "active" : ""} onClick={() => setTab("models")}>
              模型
            </button>
            <button className={tab === "security" ? "active" : ""} onClick={() => setTab("security")}>
              安全与工具
            </button>
            <button className={tab === "recovery" ? "active" : ""} onClick={() => setTab("recovery")}>
              恢复
            </button>
            <button className={tab === "status" ? "active" : ""} onClick={() => setTab("status")}>
              状态
            </button>
          </nav>
          <div className="settings-fields">
            {tab === "general" &&
              items.map((item) => (
                <SettingField key={item.key} item={item} onSave={onSave} onReset={onReset} />
              ))}
            {tab === "models" && (
              <ModelSettings
                providers={providers}
                providerSettings={providerSettings}
                providerFields={providerFields}
                modelFields={modelFields}
                knownProviders={knownProviders}
                llmRuntime={llmRuntime}
                catalog={catalog}
                actions={actions}
                onAddModel={onAddModel}
                onRemoveModel={onRemoveModel}
                providerProtocols={providerProtocols}
                onAddProvider={onAddProvider}
                onSaveModel={onSaveModel}
                onSaveProvider={onSaveProvider}
                onSaveLlmRuntime={onSaveLlmRuntime}
              />
            )}
            {tab === "security" && (
              <>
                <h3>工作区目录</h3>
                {roots.map((root, index) => (
                  <div className="admin-row" key={root.path}>
                    <div>
                      <strong>{root.path}</strong>
                      <small>{root.access === "write" ? "可读写" : "只读"}</small>
                    </div>
                    {index > 0 && <button onClick={() => onRemoveRoot(root.path)}>移除</button>}
                  </div>
                ))}
                <div className="add-root">
                  <input
                    value={path}
                    onChange={(event) => setPath(event.target.value)}
                    placeholder="额外目录路径"
                  />
                  <select value={access} onChange={(event) => setAccess(event.target.value)}>
                    <option value="read">只读</option>
                    <option value="write">读写</option>
                  </select>
                  <button
                    onClick={() => {
                      if (path.trim()) {
                        onAddRoot(path, access);
                        setPath("");
                      }
                    }}
                  >
                    添加
                  </button>
                </div>
                <h3>学习规则</h3>
                {rules.length ? (
                  rules.map((rule) => (
                    <div className="admin-row" key={rule.rule_id}>
                      <div>
                        <strong>{rule.label || rule.rule_id}</strong>
                        <small>
                          {rule.scope} · {rule.match.mode}
                        </small>
                      </div>
                      <button onClick={() => onRevokeRule(rule.rule_id)}>撤销</button>
                    </div>
                  ))
                ) : (
                  <p className="empty-copy">没有工作区学习规则。</p>
                )}
                <div className="add-root prune-row">
                  <button onClick={actions.onPruneRules}>清理已过期 / 已撤销的规则</button>
                </div>
                <h3>当前模式下模型可见的工具</h3>
                <p className="field-help">工具集合由模式的能力上界决定; 换模式会改变这份清单。</p>
                {catalog.tools.map((tool) => (
                  <details className="model-editor tool-entry" key={tool.name}>
                    <summary>
                      <span>
                        <strong>{tool.name}</strong>
                        <small>{tool.title}</small>
                      </span>
                      <small>{tool.declared_capabilities.join(" · ") || "无声明能力"}</small>
                    </summary>
                    <div className="model-fields">
                      <p className="field-help">{tool.description}</p>
                    </div>
                  </details>
                ))}
                {!catalog.tools.length && <p className="empty-copy">当前模式下没有可见工具。</p>}
              </>
            )}
            {tab === "recovery" && (
              <>
                <h3>恢复层状态</h3>
                <div className="admin-row">
                  <div>
                    <strong>恢复点总数 {catalog.recovery?.checkpoint_count ?? 0}</strong>
                    <small>
                      {catalog.recovery?.pending.length
                        ? `${catalog.recovery.pending.length} 个未收尾事务, 可能已发生部分修改`
                        : "没有未收尾的事务"}
                    </small>
                  </div>
                  <button onClick={actions.onUndo} disabled={!checkpoints.length}>
                    撤销最近一次
                  </button>
                </div>
                {catalog.recovery?.pending.map((item) => (
                  <div className="admin-row warning-row" key={item.checkpoint_id}>
                    <div>
                      <strong>{item.checkpoint_id}</strong>
                      <small>
                        {item.status} · {item.created_at}
                      </small>
                    </div>
                    <button onClick={() => onRestore(item.checkpoint_id)}>恢复</button>
                  </div>
                ))}
                <h3>恢复点</h3>
                {checkpoints.length ? (
                  checkpoints.map((checkpoint) => (
                    <CheckpointRow
                      key={checkpoint.checkpoint_id}
                      checkpoint={checkpoint}
                      onRestore={onRestore}
                      onPreview={actions.onPreviewCheckpoint}
                    />
                  ))
                ) : (
                  <p className="empty-copy">当前工作区没有恢复点。</p>
                )}
              </>
            )}
            {tab === "status" && (
              <>
                <h3>会话状态</h3>
                <div className="status-grid">
                  <div>
                    <span>会话</span>
                    <code>{catalog.status?.session_id || "-"}</code>
                  </div>
                  <div>
                    <span>模式</span>
                    <code>{catalog.status?.mode || "-"}</code>
                  </div>
                  <div>
                    <span>当前模型</span>
                    <code>{catalog.status?.model || "未设置"}</code>
                  </div>
                  <div>
                    <span>最近事件</span>
                    <code>{catalog.status?.last_event_id || "-"}</code>
                  </div>
                  <div>
                    <span>运行中</span>
                    <code>{catalog.status?.busy ? "是" : "否"}</code>
                  </div>
                </div>
                <h3>可操作目录</h3>
                {(catalog.status?.workspace_roots ?? []).map((root) => (
                  <div className="admin-row" key={root}>
                    <div>
                      <strong>{root}</strong>
                    </div>
                  </div>
                ))}
              </>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}

export function SettingField({
  item,
  onSave,
  onReset,
}: {
  item: Setting;
  onSave: (item: Setting, value: string) => void;
  onReset: (item: Setting) => void;
}) {
  const [value, setValue] = useState(item.value);
  useEffect(() => setValue(item.value), [item.value]);
  const scope = item.level === "app" ? "所有项目" : "当前项目";
  // 来源与生效时机照着终端的详情页说 (ADR-0048 决策 6): 两边读的是同一份声明, 措辞
  // 也就不该各写各的。
  const source = item.overridden ? "已覆盖" : "默认";
  // 布尔项用是/否下拉，而不是让用户往输入框里敲 "true"。它的取值只有两个，
  // 而一个只能靠背字面量才填得对的输入框，等于把校验推给用户。
  const control =
    item.kind === "bool" ? (
      <select
        value={value}
        onChange={(event) => {
          setValue(event.target.value);
          onSave(item, event.target.value);
        }}
      >
        <option value="true">是</option>
        <option value="false">否</option>
      </select>
    ) : item.choices.length ? (
      <select
        value={value}
        onChange={(event) => {
          setValue(event.target.value);
          onSave(item, event.target.value);
        }}
      >
        {item.choices.map((choice) => (
          <option key={choice}>{choice}</option>
        ))}
      </select>
    ) : (
      <div>
        <input value={value} onChange={(event) => setValue(event.target.value)} />
        <button onClick={() => onSave(item, value)}>保存</button>
      </div>
    );
  // 说明放在左列名字底下，而不是作为第三个子元素：.setting-field 是
  // `justify-content: space-between` 的两列 flex，多一个子元素会把控件挤到中间，
  // 名字和说明各自换行，整行散掉。
  return (
    <label className="setting-field">
      <span>
        <strong>{item.label || item.key}</strong>
        <small>
          {scope} · {source} · {item.effect}
        </small>
        {item.help && <small className="setting-help">{item.help}</small>}
        {item.overridden && (
          <button type="button" className="setting-reset" onClick={() => onReset(item)}>
            恢复默认（{item.default || "空"}）
          </button>
        )}
      </span>
      {control}
    </label>
  );
}
