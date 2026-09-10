/** 添加模型的抽屉。
 *
 * 采样与 thinking 在**添加时**就填: 它们是这个模型怎么用的一部分, 不是事后想起来
 * 才去某个单独入口调的东西。
 */

import { useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
  Collapse,
  Drawer,
  Flex,
  Form,
  Input,
  Radio,
  Space,
  Switch,
  Tag,
  Typography,
} from "antd";
import {
  DEFAULT_TEMPERATURE,
  DEFAULT_TOP_P,
  buildInitialModelParams,
  modelPlaceholder,
  providerAvailabilityCopy,
} from "@/shared/lib/modelParams";
import { ProviderAvatar } from "@/features/settings/models/ProviderAvatar";
import type { KnownProvider, Provider } from "@/types/admin";

type ModelDraft = {
  providerId: string;
  modelId: string;
  thinking: boolean;
  thinkingEffort: string;
  temperature: string;
  topP: string;
  contextWindow: string;
  maxTokens: string;
  makeDefault: boolean;
};

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
  const [form] = Form.useForm<ModelDraft>();
  const [saving, setSaving] = useState(false);
  const [validationError, setValidationError] = useState("");
  const providerId = Form.useWatch("providerId", form) ?? knownProviders[0]?.id ?? "";
  const thinking = Form.useWatch("thinking", form);
  const makeDefault = Form.useWatch("makeDefault", form);
  const selected = knownProviders.find((item) => item.id === providerId);
  const selectedSettings = providerSettings.find((item) => item.id === providerId);
  const ready = selected?.available || !selected?.api_key_env;

  async function submit(values: ModelDraft) {
    let params: Record<string, unknown>;
    try {
      params = buildInitialModelParams(values.contextWindow ?? "", values.maxTokens ?? "", {
        temperature: values.temperature,
        topP: values.topP,
        thinkingMode: values.thinking ? "on" : "off",
        thinkingEffort: values.thinkingEffort ?? "",
      });
      setValidationError("");
    } catch (reason) {
      setValidationError((reason as Error).message);
      return;
    }
    setSaving(true);
    const modelId = values.modelId.trim();
    const created = await onAdd(values.providerId, modelId, params);
    if (created && values.makeDefault) await onSetCurrent(values.providerId, modelId);
    setSaving(false);
    if (created) onClose();
  }

  return (
    <Drawer
      open
      title="添加模型"
      onClose={onClose}
      size={500}
      keyboard={!saving}
      mask={{ closable: !saving }}
      closable={!saving}
      destroyOnHidden
      footer={
        <Flex justify="flex-end" gap="small">
          <Button onClick={onClose} disabled={saving}>
            取消
          </Button>
          <Button type="primary" loading={saving} onClick={() => form.submit()}>
            {makeDefault ? "添加并使用" : "添加模型"}
          </Button>
        </Flex>
      }
    >
      <Form<ModelDraft>
        form={form}
        layout="vertical"
        onFinish={submit}
        initialValues={{
          providerId: knownProviders[0]?.id ?? "",
          modelId: "",
          thinking: false,
          thinkingEffort: "",
          temperature: DEFAULT_TEMPERATURE,
          topP: DEFAULT_TOP_P,
          contextWindow: "",
          maxTokens: "",
          makeDefault: true,
        }}
      >
        <Form.Item name="providerId" label="选择供应商">
          <Radio.Group disabled={saving}>
            <Space direction="vertical">
              {knownProviders.map((provider) => (
                <Radio value={provider.id} key={provider.id}>
                  <Space>
                    <ProviderAvatar providerId={provider.id} label={provider.label} size="small" />
                    <Typography.Text strong>{provider.label}</Typography.Text>
                    <Tag color={provider.available || !provider.api_key_env ? "green" : "default"}>
                      {providerAvailabilityCopy(provider)}
                    </Tag>
                  </Space>
                </Radio>
              ))}
            </Space>
          </Radio.Group>
        </Form.Item>

        <Form.Item
          name="modelId"
          label="模型 ID"
          extra="填写供应商 API 使用的准确模型名称。"
          rules={[{ required: true, message: "填一个模型 ID" }]}
        >
          <Input autoFocus placeholder={modelPlaceholder(providerId)} disabled={saving} />
        </Form.Item>
        <Alert
          type={ready ? "success" : "warning"}
          showIcon
          title={`${selected?.label || "供应商"} · ${providerAvailabilityCopy(selected ?? { available: false })}`}
          description={selectedSettings?.api_base}
          action={
            <Button size="small" onClick={onEditProvider}>
              编辑连接配置
            </Button>
          }
          style={{ marginBottom: 16 }}
        />

        <Form.Item
          name="thinking"
          label="Thinking"
          valuePropName="checked"
          extra="开启后模型会先推理再回答，更准也更慢更贵。"
        >
          <Switch disabled={saving} />
        </Form.Item>
        {thinking && (
          <Form.Item
            name="thinkingEffort"
            label="思考强度"
            extra="填模型自己声明的强度名，例如 low / medium / high。"
          >
            <Input placeholder="留空用模型默认" disabled={saving} />
          </Form.Item>
        )}

        <Collapse
          size="small"
          style={{ marginBottom: 16 }}
          items={[
            {
              key: "advanced",
              label: (
                <Space>
                  <Typography.Text>高级参数</Typography.Text>
                  <Typography.Text type="secondary">添加后也可以修改</Typography.Text>
                </Space>
              ),
              children: (
                <>
                  <Form.Item name="temperature" label="温度">
                    <Input inputMode="decimal" disabled={saving} />
                  </Form.Item>
                  <Form.Item name="topP" label="top_p">
                    <Input inputMode="decimal" disabled={saving} />
                  </Form.Item>
                  <Form.Item name="contextWindow" label="上下文窗口">
                    <Input inputMode="numeric" placeholder="使用供应商默认值" disabled={saving} />
                  </Form.Item>
                  <Form.Item name="maxTokens" label="最大输出 Tokens">
                    <Input inputMode="numeric" placeholder="使用供应商默认值" disabled={saving} />
                  </Form.Item>
                </>
              ),
            },
          ]}
        />
        {validationError && (
          <Alert type="error" showIcon title={validationError} style={{ marginBottom: 16 }} />
        )}
        <Form.Item name="makeDefault" valuePropName="checked">
          <Checkbox disabled={saving}>添加后设为默认模型</Checkbox>
        </Form.Item>
      </Form>
    </Drawer>
  );
}
