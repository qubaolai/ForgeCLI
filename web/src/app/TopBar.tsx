/** 顶栏: 品牌, 当前项目, 事件流连接状态, 计划栏开关, 设置入口。 */

import { Avatar, Badge, Button, Flex, Layout, Space, Tooltip, Typography, theme } from "antd";
import type { BadgeProps } from "antd";
import { DownOutlined, ProfileOutlined, SettingOutlined } from "@ant-design/icons";
import { formatDelay, shortPath } from "@/shared/format";
import { connectionCopy } from "@/features/runEvents/useRunEvents";
import type { ConnectionState } from "@/features/runEvents/useRunEvents";
import type { Project } from "@/types/session";

/** 连接状态映射成 Badge 的四种点。文案与提示仍然来自 connectionCopy。 */
const CONNECTION_STATUS: Record<ConnectionState, BadgeProps["status"]> = {
  connecting: "processing",
  live: "success",
  retrying: "warning",
  stopped: "error",
};

export function TopBar({
  activeProject,
  pickerOpen,
  onTogglePicker,
  connection,
  retryDelay,
  planOpen,
  planProposed,
  onTogglePlan,
  onOpenSettings,
}: {
  activeProject?: Project;
  pickerOpen: boolean;
  onTogglePicker: () => void;
  connection: ConnectionState;
  retryDelay: number;
  planOpen: boolean;
  /** 有计划在等评审: 计划栏按钮上挂一个点。 */
  planProposed: boolean;
  onTogglePlan: () => void;
  onOpenSettings: () => void;
}) {
  const { token } = theme.useToken();
  return (
    <Layout.Header
      style={{
        height: 48,
        lineHeight: "48px",
        paddingInline: 12,
        background: token.colorBgContainer,
        borderBottom: `1px solid ${token.colorBorderSecondary}`,
      }}
    >
      <Flex align="center" gap="small" style={{ height: "100%" }}>
        <Space size={6}>
          <Avatar
            size={24}
            shape="square"
            style={{ background: token.colorPrimary, color: token.colorBgContainer }}
          >
            F
          </Avatar>
          <Typography.Text strong>Forge</Typography.Text>
        </Space>
        <Tooltip title={activeProject?.primary_workspace_root}>
          <Button type="text" onClick={onTogglePicker} aria-expanded={pickerOpen}>
            {activeProject ? shortPath(activeProject.primary_workspace_root) : "选择项目"}
            <DownOutlined />
          </Button>
        </Tooltip>
        <Tooltip title={connectionCopy[connection].hint}>
          <Badge status={CONNECTION_STATUS[connection]} text={connectionCopy[connection].label} />
        </Tooltip>
        {retryDelay > 0 && <Typography.Text type="secondary">{formatDelay(retryDelay)}</Typography.Text>}
        <Flex flex={1} justify="flex-end" gap={4}>
          <Badge dot={planProposed} title="有计划待评审">
            <Button
              type={planOpen ? "primary" : "text"}
              icon={<ProfileOutlined />}
              onClick={onTogglePlan}
              aria-pressed={planOpen}
              aria-label={planOpen ? "隐藏计划" : "显示计划"}
            />
          </Badge>
          <Button type="text" icon={<SettingOutlined />} onClick={onOpenSettings} aria-label="设置" />
        </Flex>
      </Flex>
    </Layout.Header>
  );
}
