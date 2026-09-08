/** 添加模型的抽屉。
 *
 * 采样与 thinking 在**添加时**就填: 它们是这个模型怎么用的一部分, 不是事后想起来
 * 才去某个单独入口调的东西。
 */

import { useState } from "react";
import type { FormEvent } from "react";
import { useEscape } from "@/shared/hooks/useEscape";
import { CheckIcon, ChevronIcon } from "@/shared/ui/icons";
import {
  DEFAULT_TEMPERATURE,
  DEFAULT_TOP_P,
  buildInitialModelParams,
  modelPlaceholder,
  providerAvailabilityCopy,
} from "@/shared/lib/modelParams";
import { ProviderMark } from "@/features/settings/models/ProviderMark";
import type { KnownProvider, Provider } from "@/types/admin";

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

  // 抽屉盖在设置面板上面, 所以 Esc 只关它, 不往下传。
  useEscape(true, onClose, true);

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
