/** 一家供应商在折叠列表里的那一项。
 *
 * 表单行由后端给 (从 ProviderConfig 的字段声明派生)。这里曾经硬编码过一份同样的
 * (字段名, 中文标签) 数组 —— 加一个字段时它不会报错, 只会在页面上少一行。
 */

import { Space, Tag, Typography } from "antd";
import type { CollapseProps } from "antd";
import { providerAvailabilityCopy } from "@/shared/lib/modelParams";
import { DraftForm } from "@/features/settings/models/DraftForm";
import { ProviderAvatar } from "@/features/settings/models/ProviderAvatar";
import type { FieldSpec, KnownProvider, Provider } from "@/types/admin";

type CollapseItem = NonNullable<CollapseProps["items"]>[number];

export function providerCollapseItem({
  provider,
  known,
  fields,
  onSaveProvider,
}: {
  provider: Provider;
  known: KnownProvider;
  fields: FieldSpec[];
  onSaveProvider: (providerId: string, changed: Record<string, string>) => void;
}): CollapseItem {
  const rows = fields.map((field) => ({
    key: field.name,
    label: field.label,
    value: String((provider as unknown as Record<string, unknown>)[field.name] ?? ""),
  }));
  // 可用性一律从 knownProviders 里取 —— 这里曾经自己编过一个 available: true,
  // 于是一家根本没配环境变量的供应商在界面上显示"密钥已就绪"。
  const ready = known.available || !known.api_key_env;
  return {
    key: provider.id,
    label: (
      <Space wrap>
        <ProviderAvatar providerId={provider.id} label={provider.name} size="small" />
        <Typography.Text strong>{provider.name}</Typography.Text>
        <Typography.Text type="secondary">{provider.api_base || "尚未配置 API 端点"}</Typography.Text>
      </Space>
    ),
    extra: <Tag color={ready ? "green" : "default"}>{providerAvailabilityCopy(known)}</Tag>,
    children: <DraftForm fields={rows} onSave={(changed) => onSaveProvider(provider.id, changed)} />,
  };
}
