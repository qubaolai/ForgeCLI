/** 一个已添加模型在折叠列表里的那一项: 参数表 + 移除入口。 */

import { Button, Flex, Popconfirm, Space, Tag, Typography } from "antd";
import type { CollapseProps } from "antd";
import { thinkingEffortList, thinkingEffortText } from "@/shared/lib/modelParams";
import { DraftForm } from "@/features/settings/models/DraftForm";
import { ProviderAvatar } from "@/features/settings/models/ProviderAvatar";
import type { FieldSpec, Model, Provider } from "@/types/admin";

type CollapseItem = NonNullable<CollapseProps["items"]>[number];

export function modelCollapseItem({
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
}): CollapseItem {
  return {
    key: `${provider.id}:${model.id}`,
    label: (
      <Space wrap>
        <ProviderAvatar providerId={provider.id} label={provider.name} size="small" />
        <Typography.Text strong>{model.id}</Typography.Text>
        {isCurrent && <Tag color="green">默认</Tag>}
        <Typography.Text type="secondary">
          {provider.name} ·{" "}
          {model.params.context_window ? `${model.params.context_window} 上下文` : "使用供应商默认参数"}
        </Typography.Text>
      </Space>
    ),
    children: (
      <Flex vertical gap="small">
        <DraftForm
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
        <Flex gap="small" align="center" justify="flex-end" wrap>
          <Typography.Text type="secondary">移除后不会删除供应商连接配置</Typography.Text>
          <Popconfirm
            title="移除这个模型？"
            description="供应商的连接配置会留着，随时可以再加回来。"
            okText="移除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={() => onRemove(provider.id, model.id)}
          >
            <Button size="small" danger>
              移除模型
            </Button>
          </Popconfirm>
        </Flex>
      </Flex>
    ),
  };
}
