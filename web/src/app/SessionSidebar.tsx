/** 左栏: 会话列表与当前项目的工作区目录。
 *
 * 删除不可撤销, 所以走一次 Popconfirm —— 后果写在气泡里, 确认之前不发请求。
 */

import { useState } from "react";
import { Avatar, Button, Flex, Layout, Menu, Popconfirm, Typography, theme } from "antd";
import { DeleteOutlined, FolderOpenOutlined, FolderOutlined, PlusOutlined } from "@ant-design/icons";
import type { Project } from "@/types/session";
import { shortPath } from "@/shared/format";
import type { ProjectSessionGroup } from "@/features/projects/useProjectSessions";

export function SessionSidebar({
  width,
  projects,
  sessionGroups,
  activeProjectId,
  onActivateProject,
  onOpenProjectPicker,
  currentSessionId,
  busy,
  deleting,
  activeProject,
  onCreate,
  onResume,
  onDelete,
  onLoadMore,
}: {
  width: number;
  projects: Project[];
  sessionGroups: Record<string, ProjectSessionGroup>;
  activeProjectId: string | null;
  onActivateProject: (projectId: string) => void;
  onOpenProjectPicker: () => void;
  currentSessionId: string | null;
  /** 有请求在跑时不许切走, 也不许删。 */
  busy: boolean;
  deleting: boolean;
  activeProject?: Project;
  onCreate: () => void;
  /** 点会话项: 带上它属于哪个项目, 非当前项目的要先切过去。 */
  onResume: (projectId: string, sessionId: string) => void;
  onDelete: (sessionId: string) => void;
  onLoadMore: (projectId: string) => Promise<void>;
}) {
  const { token } = theme.useToken();
  const [collapsedProjects, setCollapsedProjects] = useState<Record<string, boolean>>({});

  function toggleProject(projectId: string) {
    setCollapsedProjects((items) => ({ ...items, [projectId]: !items[projectId] }));
  }

  return (
    <Layout.Sider
      className="forge-session-sidebar"
      theme="light"
      width={width}
      style={{
        display: "flex",
        flexDirection: "column",
        borderInlineEnd: 0,
      }}
    >
      <Flex align="center" gap={8} className="forge-sidebar-brand">
        <Avatar
          size={26}
          shape="square"
          style={{ background: token.colorPrimary, color: token.colorBgContainer }}
        >
          F
        </Avatar>
        <Typography.Text strong>Forge</Typography.Text>
      </Flex>
      <div className="forge-sidebar-new-session">
        <Button type="primary" block icon={<PlusOutlined />} onClick={onCreate}>
          新建会话
        </Button>
      </div>
      <div className="forge-sidebar-scroll">
        <section className="forge-sidebar-projects" aria-labelledby="sidebar-projects-title">
          <Flex align="center" justify="space-between" className="forge-sidebar-section-heading">
            <Typography.Text strong type="secondary" id="sidebar-projects-title">
              项目
            </Typography.Text>
            <Button
              type="text"
              size="small"
              icon={<PlusOutlined />}
              onClick={onOpenProjectPicker}
              title="添加项目"
            ></Button>
          </Flex>
          <div className="forge-sidebar-project-list" role="list">
            {projects.map((project) => {
              const current = project.project_id === activeProjectId;
              const locked = Boolean(busy) && !current;
              const collapsed = collapsedProjects[project.project_id] ?? false;
              const group = sessionGroups[project.project_id];
              return (
                <div role="listitem" key={project.project_id}>
                  <div className="forge-sidebar-project-heading">
                    <button
                      type="button"
                      className="forge-sidebar-project-toggle"
                      onClick={() => toggleProject(project.project_id)}
                      aria-expanded={!collapsed}
                      aria-label={
                        collapsed
                          ? `展开项目 ${shortPath(project.primary_workspace_root)}`
                          : `折叠项目 ${shortPath(project.primary_workspace_root)}`
                      }
                      title={collapsed ? "展开项目" : "折叠项目"}
                    >
                      {collapsed ? (
                        <FolderOutlined aria-hidden="true" />
                      ) : (
                        <FolderOpenOutlined aria-hidden="true" />
                      )}
                    </button>
                    <button
                      type="button"
                      className={`forge-sidebar-project-row ${current ? "current" : ""}`}
                      disabled={locked}
                      onClick={() => !current && onActivateProject(project.project_id)}
                      title={project.primary_workspace_root}
                    >
                      <Typography.Text ellipsis>{shortPath(project.primary_workspace_root)}</Typography.Text>
                    </button>
                  </div>
                  {!collapsed && (
                    <div className="forge-project-session-group">
                      {!group || (group.loading && !group.items.length) ? (
                        <Typography.Text type="secondary" className="forge-session-loading">
                          加载中…
                        </Typography.Text>
                      ) : group?.items.length ? (
                        <Menu
                          mode="inline"
                          className="session-menu"
                          style={{ borderInlineEnd: "none" }}
                          selectedKeys={currentSessionId ? [currentSessionId] : []}
                          onClick={({ key }) => onResume(project.project_id, key)}
                          items={group.items.map((session) => {
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
                        <Typography.Text type="secondary" className="forge-session-empty">
                          暂无会话
                        </Typography.Text>
                      )}
                      {group?.hasMore && (
                        <Button
                          type="text"
                          className="forge-load-more"
                          loading={group.loading}
                          onClick={() => void onLoadMore(project.project_id)}
                        >
                          展开显示更多
                        </Button>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      </div>
      <Flex vertical gap={2} className="forge-workspace-info" style={{ padding: "8px 12px" }}>
        <Typography.Text strong type="secondary">
          工作区
        </Typography.Text>
        {activeProject?.workspace_roots.map((root) => (
          <Typography.Text key={root} ellipsis={{ tooltip: root }} style={{ fontSize: token.fontSizeSM }}>
            {root}
          </Typography.Text>
        ))}
      </Flex>
    </Layout.Sider>
  );
}
