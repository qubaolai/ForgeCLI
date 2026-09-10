/** 左栏: 会话列表与当前项目的工作区目录。
 *
 * 删除不可撤销, 所以走一次 Popconfirm —— 后果写在气泡里, 确认之前不发请求。
 */

import { Button, Divider, Empty, Flex, Layout, Menu, Popconfirm, Typography, theme } from "antd";
import { DeleteOutlined, PlusOutlined } from "@ant-design/icons";
import type { Project, Session } from "@/types/session";

export function SessionSidebar({
  sessions,
  currentSessionId,
  busy,
  deleting,
  activeProject,
  onCreate,
  onResume,
  onDelete,
}: {
  sessions: Session[];
  currentSessionId: string | null;
  /** 有请求在跑时不许切走, 也不许删。 */
  busy: boolean;
  deleting: boolean;
  activeProject?: Project;
  onCreate: () => void;
  onResume: (sessionId: string) => void;
  onDelete: (sessionId: string) => void;
}) {
  const { token } = theme.useToken();
  return (
    <Layout.Sider
      theme="light"
      width={240}
      style={{
        display: "flex",
        flexDirection: "column",
        borderInlineEnd: `1px solid ${token.colorBorderSecondary}`,
      }}
    >
      <Flex align="center" justify="space-between" style={{ padding: "8px 12px" }}>
        <Typography.Text type="secondary">会话</Typography.Text>
        <Button type="text" size="small" icon={<PlusOutlined />} onClick={onCreate} aria-label="新建会话" />
      </Flex>
      <div style={{ flex: 1, overflowY: "auto" }}>
        {sessions.length ? (
          <Menu
            mode="inline"
            className="session-menu"
            style={{ borderInlineEnd: "none" }}
            selectedKeys={currentSessionId ? [currentSessionId] : []}
            onClick={({ key }) => onResume(key)}
            items={sessions.map((session) => {
              const title = session.title || "未命名会话";
              return {
                key: session.session_id,
                disabled: busy && session.session_id !== currentSessionId,
                style: { height: "auto", lineHeight: 1.4, paddingBlock: 6 },
                label: (
                  <Flex align="center" justify="space-between" gap={4}>
                    <Flex vertical style={{ minWidth: 0 }}>
                      <Typography.Text ellipsis={{ tooltip: title }}>{title}</Typography.Text>
                      <Typography.Text type="secondary" style={{ fontSize: token.fontSizeSM }}>
                        {session.updated_at?.slice(0, 16).replace("T", " ")}
                      </Typography.Text>
                    </Flex>
                    <Popconfirm
                      title="删除这个会话？"
                      description={`「${title}」的计划、待办、处理过程与恢复点会一起删掉，这个会话做过的改动将无法撤销。工作区里的文件不受影响。`}
                      okText="删除"
                      cancelText="取消"
                      okButtonProps={{ danger: true, loading: deleting }}
                      disabled={busy}
                      onConfirm={() => onDelete(session.session_id)}
                    >
                      <Button
                        type="text"
                        size="small"
                        className="session-delete"
                        disabled={busy}
                        icon={<DeleteOutlined />}
                        aria-label={`删除会话 ${title}`}
                        onClick={(event) => event.stopPropagation()}
                      />
                    </Popconfirm>
                  </Flex>
                ),
              };
            })}
          />
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="发送第一条消息后，会话会出现在这里。" />
        )}
      </div>
      <Divider style={{ margin: 0 }} />
      <Flex vertical gap={2} style={{ padding: "8px 12px" }}>
        <Typography.Text type="secondary">工作区</Typography.Text>
        {activeProject?.workspace_roots.map((root) => (
          <Typography.Text key={root} ellipsis={{ tooltip: root }} style={{ fontSize: token.fontSizeSM }}>
            {root}
          </Typography.Text>
        ))}
      </Flex>
    </Layout.Sider>
  );
}
