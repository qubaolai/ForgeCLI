/** 姿态菜单: 隔离与审批两个轴, 一次点一个。 */

import { Button, Dropdown, Flex, Typography } from "antd";
import { SettingOutlined } from "@ant-design/icons";
import { approvalOptions, sandboxOptions, stanceLabel } from "@/shared/lib/stance";
import type { Stance } from "@/shared/lib/stance";

/** 一个选项两行: 上面是档位名, 下面是这一档具体允许什么。 */
function option(item: { value: string; label: string; hint: string }, axis: string) {
  return {
    key: `${axis}:${item.value}`,
    label: (
      <Flex vertical>
        <Typography.Text strong>{item.label}</Typography.Text>
        <Typography.Text type="secondary">{item.hint}</Typography.Text>
      </Flex>
    ),
  };
}

export function ModeMenu({
  value,
  disabled,
  onChange,
}: {
  value: Stance;
  disabled: boolean;
  onChange: (patch: Partial<Stance>) => void;
}) {
  return (
    <Dropdown
      disabled={disabled}
      trigger={["click"]}
      menu={{
        selectable: true,
        selectedKeys: [`sandbox:${value.sandbox}`, `approval:${value.approval}`],
        items: [
          {
            key: "sandbox",
            type: "group",
            label: "隔离 · 围栏允许什么",
            children: sandboxOptions.map((item) => option(item, "sandbox")),
          },
          {
            key: "approval",
            type: "group",
            label: "审批 · 什么时候要你点头",
            children: approvalOptions.map((item) => option(item, "approval")),
          },
        ],
        onClick: ({ key }) => {
          const [axis, next] = key.split(":");
          onChange(axis === "sandbox" ? { sandbox: next } : { approval: next });
        },
      }}
    >
      <Button disabled={disabled}>
        {stanceLabel(value)}
        <SettingOutlined aria-hidden="true" />
      </Button>
    </Dropdown>
  );
}
