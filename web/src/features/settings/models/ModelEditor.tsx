/** 一个已添加模型的参数表, 外加两步确认的移除入口。 */

import { useState } from "react";
import { ChevronIcon } from "@/shared/ui/icons";
import { thinkingEffortList, thinkingEffortText } from "@/shared/lib/modelParams";
import { DraftForm } from "@/features/settings/models/DraftForm";
import { ProviderMark } from "@/features/settings/models/ProviderMark";
import type { FieldSpec, Model, Provider } from "@/types/admin";

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
