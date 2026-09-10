/** 常规配置。每一行的中文名、说明、来源与生效时机都来自后端 SCHEMA。 */

import { useEffect, useState } from "react";
import { Button, Flex, Form, Input, Select, Space, Switch, Tag, Typography } from "antd";
import type { SettingsFeature } from "@/features/settings/useSettings";
import type { Setting } from "@/types/session";

export function GeneralTab({ settings }: { settings: SettingsFeature }) {
  return (
    <Form layout="vertical" component="div">
      {settings.settings.map((item) => (
        <SettingField key={item.key} item={item} onSave={settings.save} onReset={settings.reset} />
      ))}
    </Form>
  );
}

function SettingField({
  item,
  onSave,
  onReset,
}: {
  item: Setting;
  onSave: (item: Setting, value: string) => void;
  onReset: (item: Setting) => void;
}) {
  const [value, setValue] = useState(item.value);
  useEffect(() => setValue(item.value), [item.value]);
  function pick(next: string) {
    setValue(next);
    onSave(item, next);
  }
  const label = item.label || item.key;
  return (
    <Form.Item
      label={label}
      extra={
        <Flex vertical gap={2} align="flex-start">
          <Space size={4} wrap>
            <Tag>{item.level === "app" ? "所有项目" : "当前项目"}</Tag>
            <Tag color={item.overridden ? "blue" : "default"}>{item.overridden ? "已覆盖" : "默认"}</Tag>
            <Typography.Text type="secondary">{item.effect}</Typography.Text>
          </Space>
          {item.help && <Typography.Text type="secondary">{item.help}</Typography.Text>}
          {item.overridden && (
            <Button type="link" size="small" style={{ padding: 0 }} onClick={() => onReset(item)}>
              恢复默认（{item.default || "空"}）
            </Button>
          )}
        </Flex>
      }
    >
      {item.kind === "bool" ? (
        <Switch aria-label={label} checked={value === "true"} onChange={(on) => pick(String(on))} />
      ) : item.choices.length ? (
        <Select
          aria-label={label}
          value={value}
          onChange={pick}
          options={item.choices.map((choice) => ({ value: choice, label: choice }))}
        />
      ) : (
        <Space.Compact block>
          <Input
            aria-label={label}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onPressEnter={() => onSave(item, value)}
          />
          <Button type="primary" onClick={() => onSave(item, value)}>
            保存
          </Button>
        </Space.Compact>
      )}
    </Form.Item>
  );
}
