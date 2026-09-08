/** 添加供应商的抽屉。端点讲 OpenAI 兼容协议就能接入, 不需要改代码。 */

import { useState } from "react";
import type { FormEvent } from "react";
import type { ProviderProtocol } from "@/types/admin";

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
