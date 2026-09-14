/** 模式菜单: 只列预设, 点一个换整档.
 *
 * 与终端 /mode 同一个形状 (interfaces/tui/commands/mode.py, ADR-0052): 隔离档与审批档
 * 两个轴不再单独露出. 早先这里是"预设 / 隔离 / 审批"三块, 于是同一个问题在两处能得到
 * 不同答案 —— 预设里选了 Auto, 审批那一块看到的却是轴上的另一个名字.
 *
 * 列表来自 GET /modes: Full Access 只在 forge 启动时带了 --allow-full-access 才在里面,
 * 没放开就不画, 而不是画出来再报错.
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
}: {
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
