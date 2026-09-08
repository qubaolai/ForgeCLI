/** 模型页: 当前默认模型, 已添加的模型, 以及按用途覆盖的高级路由。 */

import { useMemo } from "react";
import { ChevronIcon } from "@/shared/ui/icons";
import { modelRef, splitModelRef } from "@/shared/lib/modelParams";
import type { Administration } from "@/features/administration/useAdministration";
import { ModelEditor } from "@/features/settings/models/ModelEditor";
import { ProviderMark } from "@/features/settings/models/ProviderMark";

export function ModelsView({
  admin,
  onOpenAdd,
  onGoProviders,
}: {
  admin: Administration;
  onOpenAdd: () => void;
  onGoProviders: () => void;
}) {
  const configured = useMemo(
    () =>
      admin.providers.flatMap((provider) =>
        provider.models.map((model) => ({ provider, model, ref: modelRef(provider.id, model.id) })),
      ),
    [admin.providers],
  );
  const configuredRefs = configured.map((item) => item.ref);
  const [currentProviderId, currentModelId] = splitModelRef(admin.currentModel);
  const current = configured.find((item) => item.ref === admin.currentModel);
  const currentProvider = admin.knownProviders.find((item) => item.id === currentProviderId);
  const overrideCount = Object.keys(admin.overrides).length;

  return (
    <>
      <div className="model-page-heading">
        <div>
          <h3>模型</h3>
          <p>管理已接入的模型，并指定 Forge 默认使用的模型。</p>
        </div>
        <button type="button" className="primary-action" onClick={() => onOpenAdd()}>
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
            value={admin.currentModel}
            onChange={(event) => {
              const [providerId, modelId] = splitModelRef(event.target.value);
              void admin.chooseCurrentModel(providerId, modelId);
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
          <button type="button" onClick={() => onGoProviders()}>
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
                fields={admin.modelFields}
                isCurrent={ref === admin.currentModel}
                onSave={admin.saveModelFields}
                onRemove={admin.removeModel}
              />
            ))}
          </div>
        ) : (
          <div className="model-empty-state">
            <ProviderMark providerId="none" label="＋" />
            <strong>还没有模型</strong>
            <p>添加第一个模型后，Forge 才能开始对话和执行任务。</p>
            <button type="button" className="primary-action" onClick={() => onOpenAdd()}>
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
          {admin.origins.map((origin) => (
            <div className="admin-row" key={origin}>
              <div>
                <strong>{origin}</strong>
                <small>{admin.overrides[origin] || "使用默认模型"}</small>
              </div>
              <div className="row-actions">
                <select
                  value={admin.overrides[origin] ?? ""}
                  onChange={(event) => {
                    const [providerId, modelId] = splitModelRef(event.target.value);
                    admin.setModelOverride(origin, providerId, modelId);
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
                <button onClick={() => admin.clearModelOverride(origin)} disabled={!admin.overrides[origin]}>
                  清除
                </button>
              </div>
            </div>
          ))}
        </div>
      </details>
    </>
  );
}
