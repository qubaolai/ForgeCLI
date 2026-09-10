/** 安全与工具页: 工作区目录, 学习规则, 当前模式下模型看得见的工具。 */

import { useState } from "react";
import {
  Button,
  Collapse,
  Empty,
  Flex,
  Input,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { Administration } from "@/features/administration/useAdministration";
import type { LearnedRule, WorkspaceRoot } from "@/types/admin";

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
            title: "权限",
            dataIndex: "access",
            width: 96,
            render: (access: string) => <Tag>{access === "write" ? "可读写" : "只读"}</Tag>,
          },
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

      <Table<LearnedRule>
        size="small"
        pagination={false}
        rowKey="rule_id"
        title={() => <Typography.Text strong>学习规则</Typography.Text>}
        footer={() => (
          <Button size="small" onClick={admin.pruneRules}>
            清理已过期 / 已撤销的规则
          </Button>
        )}
        dataSource={admin.rules}
        locale={{
          emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有工作区学习规则。" />,
        }}
        columns={[
          { title: "规则", dataIndex: "rule_id", render: (id: string, rule) => rule.label || id },
          { title: "范围", dataIndex: "scope", width: 120 },
          { title: "匹配", dataIndex: ["match", "mode"], width: 120 },
          {
            title: "",
            key: "action",
            width: 88,
            render: (_value, rule) => (
              <Button size="small" onClick={() => admin.revokeRule(rule.rule_id)}>
                撤销
              </Button>
            ),
          },
        ]}
      />

      <Flex vertical gap="small">
        <Typography.Text strong>当前模式下模型可见的工具</Typography.Text>
        <Typography.Text type="secondary">
          工具集合由模式的能力上界决定; 换模式会改变这份清单。
        </Typography.Text>
        {admin.tools.length ? (
          <Collapse
            size="small"
            items={admin.tools.map((tool) => ({
              key: tool.name,
              label: (
                <Space wrap>
                  <Typography.Text strong>{tool.name}</Typography.Text>
                  <Typography.Text type="secondary">{tool.title}</Typography.Text>
                </Space>
              ),
              extra: (
                <Typography.Text type="secondary">
                  {tool.declared_capabilities.join(" · ") || "无声明能力"}
                </Typography.Text>
              ),
              children: <Typography.Text type="secondary">{tool.description}</Typography.Text>,
            }))}
          />
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前模式下没有可见工具。" />
        )}
      </Flex>
    </Flex>
  );
}

function AddRootRow({ onAdd }: { onAdd: (path: string, access: string) => void }) {
  const [path, setPath] = useState("");
  const [access, setAccess] = useState("read");
  return (
    <Space.Compact block>
      <Input
        value={path}
        onChange={(event) => setPath(event.target.value)}
        placeholder="额外目录路径"
        aria-label="额外目录路径"
      />
      <Select
        value={access}
        onChange={setAccess}
        style={{ width: 110 }}
        options={[
          { value: "read", label: "只读" },
          { value: "write", label: "读写" },
        ]}
      />
      <Button
        type="primary"
        icon={<PlusOutlined />}
        disabled={!path.trim()}
        onClick={() => {
          onAdd(path, access);
          setPath("");
        }}
      >
        添加
      </Button>
    </Space.Compact>
  );
}
