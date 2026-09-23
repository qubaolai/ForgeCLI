/** 模式菜单: 列模式, 点一个换档 (ADR-0055 决策 1).
 *
 * 与终端 /mode 同一个形状 (tui/commands/mode.py). 列表来自 GET /modes: Bypass
 * Permissions 只在 forge 启动时带了 --allow-full-access 才在里面, 没放开就不画,
 * 而不是画出来再报错.
 */

import { Button, Dropdown, Flex, Typography } from "antd";
import { SettingOutlined } from "@ant-design/icons";
import { presetOf, stanceLabel } from "@/shared/lib/stance";
import type { ModePreset, Stance } from "@/shared/lib/stance";

/** 一个选项两行: 上面是档位名, 下面是这一档具体允许什么. */
function option(item: ModePreset) {
  return {
    key: item.value,
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
  presets,
  disabled,
  onChange,
  onOpen,
}: {
  onOpen: () => void;
  value: Stance;
  presets: ModePreset[];
  disabled: boolean;
  /** 收预设名, 原样送 POST /mode. */
  onChange: (mode: string) => void;
}) {
  const current = presetOf(value, presets);
  return (
    <Dropdown
      disabled={disabled}
      trigger={["click"]}
      onOpenChange={(open) => {
        if (open) onOpen();
      }}
      menu={{
        selectable: true,
        selectedKeys: current ? [current.value] : [],
        items: presets.map(option),
        onClick: ({ key }) => onChange(key),
      }}
    >
      <Button disabled={disabled}>
        {stanceLabel(value, presets)}
        <SettingOutlined aria-hidden="true" />
      </Button>
    </Dropdown>
  );
}
