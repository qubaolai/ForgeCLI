/** 已信任项目的卡片墙, 外加"信任一个新目录"的那张卡。
 *
 * 落地页与顶栏的切换浮层用的是同一份: 两处各画一遍的话, 加一个字段只会有一处跟上。
 */

import { Avatar, Button, Card, Col, Form, Input, Row, Typography } from "antd";
import { FolderOpenOutlined, PlusOutlined } from "@ant-design/icons";
import { shortPath } from "@/shared/format";
import type { Project } from "@/types/session";

const COLUMN = { xs: 24, sm: 12, md: 8 };

export function ProjectGrid({
  projects,
  activeProjectId,
  busy,
  trustPath,
  onTrustPath,
  onTrust,
  onActivate,
}: {
  projects: Project[];
  /** 当前项目在卡片上标出来, 并且点不动。落地页没有当前项目。 */
  activeProjectId?: string | null;
  /** 有请求在跑时不许切: 切过去之后那一轮的事件没有地方可去。 */
  busy?: boolean;
  trustPath: string;
  onTrustPath: (path: string) => void;
  onTrust: () => void;
  onActivate: (projectId: string) => void;
}) {
  return (
    <Row gutter={[16, 16]} style={{ width: "100%" }}>
      {projects.map((project) => {
        const current = project.project_id === activeProjectId;
        const locked = Boolean(busy) || current;
        return (
          <Col key={project.project_id} {...COLUMN}>
            <Card
              hoverable={!locked}
              onClick={() => !locked && onActivate(project.project_id)}
              style={{ height: "100%", cursor: locked ? "default" : "pointer" }}
            >
              <Card.Meta
                avatar={<Avatar icon={<FolderOpenOutlined />} />}
                title={shortPath(project.primary_workspace_root)}
                description={
                  <Typography.Text type="secondary" ellipsis={{ tooltip: project.primary_workspace_root }}>
                    {project.primary_workspace_root}
                  </Typography.Text>
                }
              />
              <Typography.Text type={current ? "success" : undefined} style={{ display: "block" }}>
                {current ? "当前项目" : "打开项目 →"}
              </Typography.Text>
            </Card>
          </Col>
        );
      })}
      <Col {...COLUMN}>
        <Card title="信任新项目" style={{ height: "100%" }}>
          <Form layout="vertical" onFinish={onTrust}>
            <Form.Item label="本机目录的绝对路径">
              <Input
                value={trustPath}
                disabled={busy}
                onChange={(event) => onTrustPath(event.target.value)}
                placeholder="/path/to/repository"
              />
            </Form.Item>
            <Button
              type="primary"
              htmlType="submit"
              icon={<PlusOutlined />}
              disabled={busy || !trustPath.trim()}
            >
              添加并打开
            </Button>
          </Form>
        </Card>
      </Col>
    </Row>
  );
}
