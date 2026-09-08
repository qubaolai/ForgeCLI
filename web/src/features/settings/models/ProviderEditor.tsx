/** 一家供应商的连接配置。
 *
 * 表单行由后端给 (从 ProviderConfig 的字段声明派生)。这里曾经硬编码过一份同样的
 * (字段名, 中文标签) 数组 —— 加一个字段时它不会报错, 只会在页面上少一行。
 */

import { ChevronIcon } from "@/shared/ui/icons";
import { providerAvailabilityCopy } from "@/shared/lib/modelParams";
import { DraftForm } from "@/features/settings/models/DraftForm";
import { ProviderMark } from "@/features/settings/models/ProviderMark";
import type { FieldSpec, KnownProvider, Provider } from "@/types/admin";

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
