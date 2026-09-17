/** 恢复页: 文件历史的快照, 回到某条消息之前 (ADR-0054 决策 5). */

import { Alert, Button, Empty, Flex, Popconfirm, Table, Typography } from "antd";
import type { Administration } from "@/features/administration/useAdministration";
import type { RewindPoint } from "@/types/admin";

export function RecoveryTab({ admin }: { admin: Administration }) {
  return (
    <Flex vertical gap="middle">
      <Alert
        type="info"
        showIcon
        title="文件历史"
        description="每条 assistant 消息一个快照, 只记 Edit / Write 改过的文件; Bash 改的不在历史里。"
      />
      <Table<RewindPoint>
        size="small"
        pagination={false}
        rowKey="message_id"
        dataSource={admin.rewindPoints}
        locale={{
          emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有可回退的快照。" />,
        }}
        columns={[
          { title: "消息", dataIndex: "message_id", width: 200 },
          { title: "创建于", dataIndex: "created_at", width: 200 },
          {
            title: "文件",
            dataIndex: "files",
            render: (files: string[]) => (
              <Typography.Text type="secondary">{files.length ? files.join(", ") : "无"}</Typography.Text>
            ),
          },
          {
            title: "",
            key: "action",
            width: 96,
            render: (_value, point) => (
              <Popconfirm
                title="回到这条消息之前？"
                description="会覆盖这些文件此后的全部改动。"
                okText="回退"
                cancelText="取消"
                okButtonProps={{ danger: true }}
                onConfirm={() => admin.rewindTo(point.message_id)}
              >
                <Button size="small" danger>
                  回退
                </Button>
              </Popconfirm>
            ),
          },
        ]}
      />
    </Flex>
  );
}
