/** 状态页: 当前会话、模式、模型与可操作目录。全部只读。 */

import { Descriptions, Empty, Flex, Table } from "antd";
import type { Administration } from "@/features/administration/useAdministration";

export function StatusTab({ admin }: { admin: Administration }) {
  const status = admin.statusView;
  return (
    <Flex vertical gap="middle">
      <Descriptions
        title="会话状态"
        bordered
        size="small"
        column={1}
        items={[
          { key: "session", label: "会话", children: status?.session_id || "-" },
          { key: "mode", label: "模式", children: status?.mode || "-" },
          { key: "model", label: "当前模型", children: status?.model || "未设置" },
          { key: "event", label: "最近事件", children: status?.last_event_id || "-" },
          { key: "busy", label: "运行中", children: status?.busy ? "是" : "否" },
        ]}
      />
      <Table
        size="small"
        pagination={false}
        rowKey="path"
        dataSource={(status?.workspace_roots ?? []).map((path) => ({ path }))}
        columns={[{ title: "可操作目录", dataIndex: "path" }]}
        locale={{
          emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有可操作目录" />,
        }}
      />
    </Flex>
  );
}
