/** 模型页: 当前默认模型, 已添加的模型, 以及按用途覆盖的高级路由。 */

import { useMemo } from "react";
import { Button, Card, Collapse, Empty, Flex, Select, Space, Table, Tag, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { modelRef, splitModelRef } from "@/shared/lib/modelParams";
import type { Administration } from "@/features/administration/useAdministration";
import { modelCollapseItem } from "@/features/settings/models/ModelEditor";
import { ProviderAvatar } from "@/features/settings/models/ProviderAvatar";

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
  const options = configured.map((item) => ({ value: item.ref, label: item.ref }));
  const [currentProviderId, currentModelId] = splitModelRef(admin.currentModel);
  const current = configured.find((item) => item.ref === admin.currentModel);
  const currentProvider = admin.knownProviders.find((item) => item.id === currentProviderId);
  const overrideCount = Object.keys(admin.overrides).length;

  return (
    <Flex vertical gap="middle">
      <Flex justify="space-between" align="flex-start" gap="small" wrap>
        <Flex vertical>
          <Typography.Text strong>模型</Typography.Text>
          <Typography.Text type="secondary">管理已接入的模型，并指定 Forge 默认使用的模型。</Typography.Text>
        </Flex>
        <Button type="primary" icon={<PlusOutlined />} onClick={onOpenAdd}>
          添加模型
        </Button>
      </Flex>

      <Card size="small">
        <Flex align="center" gap="middle" wrap>
          <ProviderAvatar providerId={currentProviderId || "none"} label={currentProvider?.label} />
          <Flex vertical style={{ flex: 1, minWidth: 160 }}>
            <Typography.Text type="secondary">当前默认模型</Typography.Text>
            <Typography.Text strong>{currentModelId || "尚未设置"}</Typography.Text>
            <Typography.Text type="secondary">
              {current
                ? `${current.provider.name} · 所有未单独指定的任务默认使用`
                : "添加模型后即可设为默认模型"}
            </Typography.Text>
          </Flex>
          {options.length > 0 && (
            <Select
              aria-label="更换默认模型"
              style={{ minWidth: 220 }}
              value={admin.currentModel || undefined}
              placeholder="选择模型"
              options={options}
              onChange={(value) => {
                const [providerId, modelId] = splitModelRef(value);
                void admin.chooseCurrentModel(providerId, modelId);
              }}
            />
          )}
        </Flex>
      </Card>

      <Flex justify="space-between" align="flex-start" gap="small" wrap>
        <Flex vertical>
          <Space size={4}>
            <Typography.Text strong>已添加模型</Typography.Text>
            <Tag>{configured.length}</Tag>
          </Space>
          <Typography.Text type="secondary">展开模型可编辑参数；修改后统一保存。</Typography.Text>
        </Flex>
        <Button onClick={onGoProviders}>管理供应商 →</Button>
      </Flex>
      {configured.length ? (
        <Collapse
          size="small"
          items={configured.map(({ provider, model, ref }) =>
            modelCollapseItem({
              provider,
              model,
              fields: admin.modelFields,
              isCurrent: ref === admin.currentModel,
              onSave: admin.saveModelFields,
              onRemove: admin.removeModel,
            }),
          )}
        />
      ) : (
        <Empty description="还没有模型。添加第一个模型后，Forge 才能开始对话和执行任务。">
          <Button type="primary" icon={<PlusOutlined />} onClick={onOpenAdd}>
            添加模型
          </Button>
        </Empty>
      )}

      <Collapse
        size="small"
        items={[
          {
            key: "routing",
            label: (
              <Space wrap>
                <Typography.Text strong>高级路由</Typography.Text>
                <Typography.Text type="secondary">Thinking 与按任务用途覆盖默认模型</Typography.Text>
              </Space>
            ),
            extra: <Tag>{overrideCount ? `${overrideCount} 项已配置` : "使用默认模型"}</Tag>,
            children: (
              <Table<{ origin: string }>
                size="small"
                pagination={false}
                rowKey="origin"
                title={() => (
                  <Flex vertical>
                    <Typography.Text strong>用途模型覆盖</Typography.Text>
                    <Typography.Text type="secondary">未设置的用途自动使用当前默认模型。</Typography.Text>
                  </Flex>
                )}
                dataSource={admin.origins.map((origin) => ({ origin }))}
                columns={[
                  { title: "用途", dataIndex: "origin" },
                  {
                    title: "覆盖模型",
                    key: "override",
                    render: (_value, row) => (
                      <Select
                        style={{ minWidth: 200 }}
                        value={admin.overrides[row.origin] || undefined}
                        placeholder="使用默认模型"
                        options={options}
                        onChange={(value) => {
                          const [providerId, modelId] = splitModelRef(value);
                          admin.setModelOverride(row.origin, providerId, modelId);
                        }}
                      />
                    ),
                  },
                  {
                    title: "",
                    key: "action",
                    width: 88,
                    render: (_value, row) => (
                      <Button
                        size="small"
                        onClick={() => admin.clearModelOverride(row.origin)}
                        disabled={!admin.overrides[row.origin]}
                      >
                        清除
                      </Button>
                    ),
                  },
                ]}
              />
            ),
          },
        ]}
      />
    </Flex>
  );
}
