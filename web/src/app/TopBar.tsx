/** 顶栏: 事件流连接状态, 计划栏开关, 设置入口。 */

import { Badge, Button, Flex, Layout, Tooltip, Typography, theme } from "antd";
import type { BadgeProps } from "antd";
import { ProfileOutlined, SettingOutlined } from "@ant-design/icons";
import { formatDelay } from "@/shared/format";
import { connectionCopy } from "@/features/runEvents/useRunEvents";
import type { ConnectionState } from "@/features/runEvents/useRunEvents";

/** 连接状态映射成 Badge 的四种点。文案与提示仍然来自 connectionCopy。 */
const CONNECTION_STATUS: Record<ConnectionState, BadgeProps["status"]> = {
  connecting: "processing",
  live: "success",
  retrying: "warning",
  stopped: "error",
};

export function TopBar({
  connection,
  retryDelay,
  planOpen,
  planProposed,
  onTogglePlan,
  onOpenSettings,
}: {
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
      className="forge-topbar"
      style={{
        height: 52,
        lineHeight: "52px",
        paddingInline: 16,
        background: token.colorBgContainer,
        borderBottom: `1px solid ${token.colorBorderSecondary}`,
      }}
    >
      <Flex align="center" gap="middle" className="forge-topbar-inner" style={{ height: "100%" }}>
        <Tooltip title={connectionCopy[connection].hint}>
          <span className="forge-connection">
            <Badge status={CONNECTION_STATUS[connection]} text={connectionCopy[connection].label} />
          </span>
        </Tooltip>
        {retryDelay > 0 && <Typography.Text type="secondary">{formatDelay(retryDelay)}</Typography.Text>}
        <Flex flex={1} justify="flex-end" gap={4} className="forge-topbar-actions">
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
