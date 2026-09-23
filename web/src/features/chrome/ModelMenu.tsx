/** 模型菜单: 当前模型, 以及这个模型的思考开关与强度。 */

import { useState } from "react";
import { Button, Spin, Empty, Flex, Popover, Select, Switch, Tag, Typography } from "antd";
import { MoreOutlined } from "@ant-design/icons";
import type { ThinkingView } from "@/types/admin";

export function ModelMenu({
  onOpen,
  current,
  models,
  thinking,
  disabled,
  onChoose,
  onThinking,
}: {
  onOpen: () => Promise<void>;
  current: string;
  models: string[];
  thinking: ThinkingView;
  disabled: boolean;
  onChoose: (providerId: string, modelId: string) => void;
  onThinking: (mode: string, effort: string) => void;
}) {
  const [loading, setLoading] = useState(false);
  const efforts = thinking.supported_efforts ?? [];
  const thinkingOn = thinking.mode === "on";
  const label = current ? current.split(":").slice(1).join(":") || current : "选择模型";
  return (
    <Popover
      trigger="click"
      onOpenChange={(open) => {
        if (!open) return;
        setLoading(true);
        void onOpen().finally(() => setLoading(false));
      }}
      title="模型与思考"
      content={
        <Spin spinning={loading}>
          <Flex vertical gap="middle" style={{ width: 280, maxWidth: "calc(100vw - 64px)" }}>
            <Select
              aria-label="当前模型"
              showSearch
              disabled={disabled || loading}
              value={current || undefined}
              placeholder="选择模型"
              options={models.map((value) => ({ value, label: value }))}
              notFoundContent={<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="去设置页添加模型" />}
              onChange={(ref: string) => {
                const [provider, ...rest] = ref.split(":");
                onChoose(provider, rest.join(":"));
              }}
            />
            {thinking.configured && (
              <>
                <Flex justify="space-between" align="center">
                  <Typography.Text>思考</Typography.Text>
                  <Switch
                    aria-label="启用思考"
                    checked={thinkingOn}
                    disabled={disabled || loading}
                    onChange={(on) => onThinking(on ? "on" : "off", "")}
                  />
                </Flex>
                {thinkingOn &&
                  (efforts.length ? (
                    <Select
                      aria-label="思考强度"
                      disabled={disabled || loading}
                      value={thinking.effort || undefined}
                      placeholder="模型默认强度"
                      options={efforts.map((value) => ({ value, label: value }))}
                      onChange={(value) => onThinking("", value)}
                    />
                  ) : (
                    <Typography.Text type="secondary">该模型未声明可用强度。</Typography.Text>
                  ))}
              </>
            )}
          </Flex>
        </Spin>
      }
    >
      <Button disabled={disabled || loading} title={current || "未设置模型"}>
        <Typography.Text ellipsis style={{ maxWidth: 160 }}>
          {label}
        </Typography.Text>
        {thinkingOn && <Tag color="blue">思考{thinking.effort ? ` · ${thinking.effort}` : ""}</Tag>}
        <MoreOutlined aria-hidden="true" />
      </Button>
    </Popover>
  );
}
