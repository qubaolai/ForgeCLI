/** 添加供应商的抽屉。端点讲 OpenAI 兼容协议就能接入, 不需要改代码。 */

import { useState } from "react";
import { Button, Drawer, Flex, Form, Input, Select } from "antd";
import type { ProviderProtocol } from "@/types/admin";

type ProviderDraft = {
  provider_id: string;
  name: string;
  api_base: string;
  protocol: string;
  api_key_env: string;
};

export function AddProviderDrawer({
  protocols,
  onClose,
  onAdd,
}: {
  protocols: ProviderProtocol[];
  onClose: () => void;
  onAdd: (body: ProviderDraft) => Promise<boolean>;
}) {
  const [form] = Form.useForm<ProviderDraft>();
  const [saving, setSaving] = useState(false);
  const supported = protocols.find((item) => item.supported)?.value ?? "openai_compatible";

  async function submit(values: ProviderDraft) {
    setSaving(true);
    const done = await onAdd({
      provider_id: values.provider_id.trim(),
      name: values.name.trim(),
      api_base: values.api_base.trim(),
      protocol: values.protocol,
      api_key_env: values.api_key_env.trim(),
    });
    setSaving(false);
    if (done) onClose();
  }

  return (
    <Drawer
      open
      title="添加供应商"
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
            添加供应商
          </Button>
        </Flex>
      }
    >
      <Form<ProviderDraft>
        form={form}
        layout="vertical"
        onFinish={submit}
        initialValues={{ provider_id: "", name: "", api_base: "", protocol: supported, api_key_env: "" }}
      >
        {/* 不支持的也列出来并禁用: 看不到这一项会让人以为 Forge 不打算支持, 于是去找别的工具。 */}
        <Form.Item
          name="protocol"
          label="端点协议"
          extra="目前只有 OpenAI Compatible 有对应的适配器，另外两种还没有。"
        >
          <Select
            disabled={saving}
            options={protocols.map((item) => ({
              value: item.value,
              label: `${item.label}${item.supported ? "" : "（暂不支持）"}`,
              disabled: !item.supported,
            }))}
          />
        </Form.Item>
        <Form.Item
          name="provider_id"
          label="供应商 ID"
          extra="小写标识符，配置文件与模型引用都用它。"
          rules={[{ required: true, message: "填一个小写标识符" }]}
        >
          <Input autoFocus placeholder="acme" disabled={saving} />
        </Form.Item>
        <Form.Item name="name" label="展示名" extra="留空则用 ID。">
          <Input placeholder="Acme" disabled={saving} />
        </Form.Item>
        <Form.Item
          name="api_base"
          label="API 地址"
          extra="完整的 /chat/completions 端点。"
          rules={[{ required: true, message: "填一个 API 地址" }]}
        >
          <Input placeholder="https://acme.example.com/v1/chat/completions" disabled={saving} />
        </Form.Item>
        <Form.Item
          name="api_key_env"
          label="API Key 环境变量"
          extra="Forge 只读环境变量名，不保存密钥本身。免密钥的本地端点可留空。"
        >
          <Input placeholder="ACME_API_KEY" disabled={saving} />
        </Form.Item>
      </Form>
    </Drawer>
  );
}
