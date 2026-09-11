/** 已信任项目的导航列表, 外加添加本地目录的面板。
 *
 * 落地页与顶栏的切换浮层用的是同一份: 两处各画一遍的话, 加一个字段只会有一处跟上。
 */

import { useState } from "react";
import { Avatar, Button, Flex, Form, Input, Typography } from "antd";
import { CheckOutlined, FolderOpenOutlined, PlusOutlined } from "@ant-design/icons";
import { shortPath } from "@/shared/format";
import type { Project } from "@/types/session";

export function ProjectGrid({
  projects,
  activeProjectId,
  busy,
  onTrust,
  onActivate,
}: {
  projects: Project[];
  /** 当前项目在卡片上标出来, 并且点不动。落地页没有当前项目。 */
  activeProjectId?: string | null;
  /** 有请求在跑时不许切: 切过去之后那一轮的事件没有地方可去。 */
  busy?: boolean;
  onTrust: (path: string) => Promise<void>;
  onActivate: (projectId: string) => void;
}) {
  const [trustPath, setTrustPath] = useState("");
  const [opening, setOpening] = useState(false);

  async function trustAndOpen() {
    const path = trustPath.trim();
    if (!path) return;
    setOpening(true);
    try {
      await onTrust(path);
    } finally {
      setOpening(false);
    }
  }

  return (
    <div className="forge-project-picker-layout">
      <section className="forge-project-list-panel" aria-labelledby="project-list-title">
        <Flex align="center" justify="space-between" className="forge-project-list-heading">
          <Typography.Text strong id="project-list-title">
            我的项目
          </Typography.Text>
          <Typography.Text type="secondary">{projects.length}</Typography.Text>
        </Flex>
        <div className="forge-project-list" role="list">
          {projects.length ? (
            projects.map((project) => {
              const current = project.project_id === activeProjectId;
              const locked = Boolean(busy) || current;
              return (
                <div role="listitem" key={project.project_id}>
                  <button
                    type="button"
                    className={`forge-project-row ${current ? "current" : ""}`}
                    disabled={locked}
                    onClick={() => onActivate(project.project_id)}
                    aria-current={current ? "page" : undefined}
                    title={project.primary_workspace_root}
                  >
                    <Avatar size={30} shape="square" icon={<FolderOpenOutlined />} />
                    <Flex vertical className="forge-project-row-copy">
                      <Typography.Text strong ellipsis>
                        {shortPath(project.primary_workspace_root)}
                      </Typography.Text>
                      <Typography.Text type="secondary" ellipsis>
                        {project.primary_workspace_root}
                      </Typography.Text>
                    </Flex>
                    <span className="forge-project-row-action">
                      {current && <CheckOutlined aria-label="当前项目" />}
                    </span>
                  </button>
                </div>
              );
            })
          ) : (
            <div className="forge-project-list-empty">
              <Typography.Text type="secondary">还没有已信任的项目</Typography.Text>
            </div>
          )}
        </div>
      </section>

      <section className="forge-add-project-panel" aria-labelledby="add-project-title">
        <Flex align="flex-start" gap={12}>
          <Avatar size={34} shape="square" icon={<FolderOpenOutlined />} />
          <Flex vertical gap={2}>
            <Typography.Text strong id="add-project-title">
              添加本地项目
            </Typography.Text>
            <Typography.Text type="secondary">输入本机项目目录的绝对路径，添加后会自动打开。</Typography.Text>
          </Flex>
        </Flex>
        <div className="forge-add-project-action">
          <Form layout="vertical" requiredMark={false} onFinish={() => void trustAndOpen()}>
            <Form.Item label="项目目录绝对路径">
              <Input
                value={trustPath}
                disabled={busy || opening}
                placeholder="/path/to/repository"
                onChange={(event) => setTrustPath(event.target.value)}
              />
            </Form.Item>
            <Button
              type="primary"
              htmlType="submit"
              icon={<PlusOutlined />}
              loading={opening}
              disabled={busy || !trustPath.trim()}
            >
              添加并打开
            </Button>
          </Form>
        </div>
      </section>
    </div>
  );
}
