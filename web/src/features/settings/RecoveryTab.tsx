/** 恢复页: 未收尾的事务, 恢复点列表, 以及撤销最近一次。 */

import { useState } from "react";
import { Alert, Button, Empty, Flex, Popconfirm, Table, Typography } from "antd";
import type { Administration } from "@/features/administration/useAdministration";
import type { Checkpoint } from "@/types/admin";

export function RecoveryTab({ admin }: { admin: Administration }) {
  // 预览按恢复点缓存: 展开过一次就不再重复请求。
  const [previews, setPreviews] = useState<Record<string, string>>({});
  const pending = admin.recovery?.pending ?? [];

  return (
    <Flex vertical gap="middle">
      <Alert
        type={pending.length ? "warning" : "info"}
        showIcon
        title={`恢复点总数 ${admin.recovery?.checkpoint_count ?? 0}`}
        description={
          pending.length ? `${pending.length} 个未收尾事务, 可能已发生部分修改` : "没有未收尾的事务"
        }
        action={
          <Popconfirm
            title="撤销最近一次改动？"
            okText="撤销"
            cancelText="取消"
            onConfirm={admin.undoLatest}
            disabled={!admin.checkpoints.length}
          >
            <Button size="small" disabled={!admin.checkpoints.length}>
              撤销最近一次
            </Button>
          </Popconfirm>
        }
      />
      {pending.map((item) => (
        <Alert
          key={item.checkpoint_id}
          type="warning"
          showIcon
          title={item.checkpoint_id}
          description={`${item.status} · ${item.created_at}`}
          action={
            <Button size="small" onClick={() => admin.restoreCheckpoint(item.checkpoint_id)}>
              恢复
            </Button>
          }
        />
      ))}
      <Table<Checkpoint>
        size="small"
        pagination={false}
        rowKey="checkpoint_id"
        title={() => <Typography.Text strong>恢复点</Typography.Text>}
        dataSource={admin.checkpoints}
        locale={{
          emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前工作区没有恢复点。" />,
        }}
        expandable={{
          expandedRowRender: (checkpoint) => (
            <Typography>
              <pre style={{ margin: 0, maxHeight: 220, overflow: "auto" }}>
                {previews[checkpoint.checkpoint_id] ?? "读取中…"}
              </pre>
            </Typography>
          ),
          onExpand: (expanded, checkpoint) => {
            if (!expanded || previews[checkpoint.checkpoint_id]) return;
            void admin
              .previewCheckpoint(checkpoint.checkpoint_id)
              .then((text) => setPreviews((state) => ({ ...state, [checkpoint.checkpoint_id]: text })));
          },
        }}
        columns={[
          { title: "恢复点", dataIndex: "checkpoint_id" },
          { title: "状态", dataIndex: "status", width: 96 },
          { title: "策略", dataIndex: "snapshot_strategy", width: 120 },
          { title: "时间", dataIndex: "created_at", width: 180 },
          {
            title: "",
            key: "action",
            width: 88,
            render: (_value, checkpoint) => (
              <Popconfirm
                title="恢复到这个恢复点？"
                description="工作区里这之后的改动会被覆盖。"
                okText="恢复"
                cancelText="取消"
                onConfirm={() => admin.restoreCheckpoint(checkpoint.checkpoint_id)}
              >
                <Button size="small">恢复</Button>
              </Popconfirm>
            ),
          },
        ]}
      />
    </Flex>
  );
}
