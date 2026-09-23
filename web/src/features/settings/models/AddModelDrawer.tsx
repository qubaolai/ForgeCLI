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
  Select,
  Space,
  Switch,
  Tag,
  Typography,
} from "antd";
import {
  DEFAULT_CONTEXT_WINDOW,
  DEFAULT_MAX_TOKENS,
  DEFAULT_TEMPERATURE,
  DEFAULT_TOP_P,
  buildInitialModelParams,
  modelPlaceholder,
  providerAvailabilityCopy,
  providerReady,
} from "@/shared/lib/modelParams";
import { ProviderAvatar } from "@/features/settings/models/ProviderAvatar";
import { CapabilitySelect } from "@/features/settings/models/CapabilitySelect";
import type { KnownProvider, Provider } from "@/types/admin";

type ModelDraft = {
  providerId: string;
  modelId: string;
  thinking: boolean;
  thinkingEffort: string;
  thinkingEfforts: string;
  temperature: string;
  topP: string;
  contextWindow: string;
  maxTokens: string;
  supportsStructuredOutput: string;
  supportsJsonObject: string;
  supportsToolCalling: string;
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
  const thinkingEfforts = Form.useWatch("thinkingEfforts", form) ?? "";
  const effortOptions = thinkingEfforts
    .split(",")
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean);
  const makeDefault = Form.useWatch("makeDefault", form);
  const selected = knownProviders.find((item) => item.id === providerId);
  const selectedSettings = providerSettings.find((item) => item.id === providerId);
  const ready = selected ? providerReady(selected) : false;

  async function submit(values: ModelDraft) {
    let params: Record<string, unknown>;
    try {
      params = buildInitialModelParams(values.contextWindow ?? "", values.maxTokens ?? "", {
        temperature: values.temperature,
        topP: values.topP,
        thinkingMode: values.thinking ? "on" : "off",
        thinkingEffort: values.thinkingEffort ?? "",
      });
      params.thinking_efforts = (values.thinkingEfforts ?? "")
        .split(",")
        .map((item) => item.trim().toLowerCase())
        .filter(Boolean);
      params.supports_structured_output = values.supportsStructuredOutput === "true";
      params.supports_json_object = values.supportsJsonObject === "true";
      params.supports_tool_calling = values.supportsToolCalling === "true";
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
        className="forge-model-fields"
        form={form}
        layout="vertical"
        onFinish={submit}
        initialValues={{
          providerId: knownProviders[0]?.id ?? "",
          modelId: "",
          thinking: false,
          thinkingEffort: "",
          thinkingEfforts: "",
          temperature: DEFAULT_TEMPERATURE,
          topP: DEFAULT_TOP_P,
          contextWindow: DEFAULT_CONTEXT_WINDOW,
          maxTokens: DEFAULT_MAX_TOKENS,
          supportsStructuredOutput: "false",
          supportsJsonObject: "false",
          supportsToolCalling: "true",
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
                    <Tag color={providerReady(provider) ? "green" : "default"}>
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
          <>
            <Form.Item
              name="thinkingEfforts"
              label="模型支持的思考强度"
              extra="按供应商文档填写，逗号分隔，例如 low, medium, high。"
            >
              <Input placeholder="low, medium, high" disabled={saving} />
            </Form.Item>
            <Form.Item name="thinkingEffort" label="当前思考强度">
              <Select
                className="forge-choice-select"
                allowClear
                placeholder="留空使用模型默认"
                disabled={saving || effortOptions.length === 0}
                options={effortOptions.map((value) => ({ value, label: value }))}
              />
            </Form.Item>
          </>
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
                    <Input inputMode="numeric" disabled={saving} />
                  </Form.Item>
                  <Form.Item name="maxTokens" label="最大输出 Tokens">
                    <Input inputMode="numeric" disabled={saving} />
                  </Form.Item>
                  {(
                    [
                      ["supportsStructuredOutput", "支持 JSON Schema 输出"],
                      ["supportsJsonObject", "支持 JSON Object 输出"],
                      ["supportsToolCalling", "支持工具调用"],
                    ] as const
                  ).map(([name, label]) => (
                    <Form.Item key={name} name={name} label={label}>
                      <CapabilitySelect
                        disabled={saving}
                        detail={label.replace(/^支持\s*/, "")}
                        value={form.getFieldValue(name)}
                        onChange={(value) => form.setFieldValue(name, value)}
                      />
                    </Form.Item>
                  ))}
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
