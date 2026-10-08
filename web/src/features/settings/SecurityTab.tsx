/** 安全与工具页: 工作区目录, 权限规则, 模型看得见的工具 (ADR-0055). */

import { useState } from "react";
import { Button, Collapse, Empty, Flex, Input, Popconfirm, Space, Table, Tag, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { Administration } from "@/features/administration/useAdministration";
import type { WorkspaceRoot } from "@/types/admin";

/** 来源在界面上的名字, 与终端 /permissions 同一张表. */
const SOURCE_LABELS: Record<string, string> = {
  policy_settings: "内置 (只读)",
  user_settings: "用户 config.json",
  project_settings: "项目 forge.json",
  cli_arg: "命令行",
  session: "本会话",
};

type RuleRow = { key: string; behavior: string; source: string; rule: string };

function ruleRows(admin: Administration): RuleRow[] {
  const rows: RuleRow[] = [];
  const tables = admin.permissions?.rules ?? {};
  for (const [behavior, bySource] of Object.entries(tables)) {
    for (const [source, rules] of Object.entries(bySource)) {
      for (const rule of rules) {
        rows.push({ key: `${behavior}|${source}|${rule}`, behavior, source, rule });
      }
    }
  }
  return rows;
}

export function SecurityTab({ admin }: { admin: Administration }) {
  return (
    <Flex vertical gap="middle">
      <Table<WorkspaceRoot>
        size="small"
        pagination={false}
        rowKey="path"
        title={() => <Typography.Text strong>工作区目录</Typography.Text>}
        footer={() => <AddRootRow onAdd={admin.addWorkspace} />}
        dataSource={admin.workspaceRoots}
        columns={[
          { title: "目录", dataIndex: "path" },
          {
            title: "",
            key: "action",
            width: 88,
            // 第一个是主目录, 移不掉 —— 移掉之后这个项目就没有可操作的地方了。
            render: (_value, root, index) =>
              index > 0 ? (
                <Popconfirm
                  title="移除这个目录？"
                  description="移除后 Forge 不再能读写它。"
                  okText="移除"
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                  onConfirm={() => admin.removeWorkspace(root.path)}
                >
                  <Button size="small" danger>
                    移除
                  </Button>
                </Popconfirm>
              ) : null,
          },
        ]}
      />

      <Table<RuleRow>
        size="small"
        pagination={false}
        rowKey="key"
        title={() => (
          <Space>
            <Typography.Text strong>权限规则</Typography.Text>
            <Typography.Text type="secondary">
              写法: Bash(git commit:*), Edit(src/**); 用户与项目来源在设置文件里。
            </Typography.Text>
          </Space>
        )}
        dataSource={ruleRows(admin)}
        locale={{
          emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有任何权限规则。" />,
        }}
        columns={[
          {
            title: "行为",
            dataIndex: "behavior",
            width: 80,
            render: (behavior: string) => (
              <Tag color={behavior === "deny" ? "red" : behavior === "ask" ? "gold" : "green"}>
                {behavior}
              </Tag>
            ),
          },
          { title: "规则", dataIndex: "rule" },
          {
            title: "来源",
            dataIndex: "source",
            width: 140,
            render: (source: string) => SOURCE_LABELS[source] ?? source,
          },
          {
            title: "",
            key: "action",
            width: 88,
            render: (_value, row) =>
              row.source === "policy_settings" ? null : (
                <Button size="small" onClick={() => admin.removeRule(row.behavior, row.source, row.rule)}>
                  删除
                </Button>
              ),
          },
        ]}
      />

      <Flex vertical gap="small">
        <Typography.Text strong>模型可见的工具</Typography.Text>
        <Typography.Text type="secondary">整工具的 deny 规则会把工具从这份清单里摘掉。</Typography.Text>
        {admin.tools.length ? (
          <Collapse
            size="small"
            items={admin.tools.map((tool) => ({
              key: tool.name,
              label: <Typography.Text strong>{tool.name}</Typography.Text>,
              children: (
                <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }} type="secondary">
                  {tool.description}
                </Typography.Paragraph>
              ),
            }))}
          />
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有可见工具。" />
        )}
      </Flex>
    </Flex>
  );
}

function AddRootRow({ onAdd }: { onAdd: (path: string, access: string) => void }) {
  const [path, setPath] = useState("");
  return (
    <Space.Compact block>
      <Input
        value={path}
        onChange={(event) => setPath(event.target.value)}
        placeholder="额外目录路径 (加进来的目录读写都自动放行)"
        aria-label="额外目录路径"
      />
      <Button
        type="primary"
        icon={<PlusOutlined />}
        disabled={!path.trim()}
        onClick={() => {
          onAdd(path, "write");
          setPath("");
        }}
      >
        添加
      </Button>
    </Space.Compact>
  );
}
