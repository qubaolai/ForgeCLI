/** 还没有活动项目时的整屏落地页: 选一个已信任的项目, 或者信任一个新目录。 */

import { Avatar, Flex, Layout, Space, Typography, theme } from "antd";
import { ProjectGrid } from "@/features/projects/ProjectGrid";
import type { Project } from "@/types/session";

export function ProjectLanding({
  projects,
  onActivate,
  onTrust,
}: {
  projects: Project[];
  onActivate: (projectId: string) => void;
  onTrust: (path: string) => Promise<void>;
}) {
  const { token } = theme.useToken();
  return (
    <Layout className="forge-landing" style={{ minHeight: "100vh" }}>
      <Flex
        vertical
        align="center"
        justify="center"
        gap="large"
        className="forge-landing-inner"
        style={{ padding: 24 }}
      >
        <Space size="middle" className="forge-landing-brand">
          <Avatar size={48} shape="square" style={{ background: token.colorPrimary, fontSize: 24 }}>
            F
          </Avatar>
          <div>
            <Typography.Title level={2} style={{ margin: 0 }}>
              Forge
            </Typography.Title>
            <Typography.Text type="secondary">本地优先的 AI 工程工作台</Typography.Text>
          </div>
        </Space>
        <div className="forge-project-grid-wrap" style={{ width: "100%", maxWidth: 1080 }}>
          <ProjectGrid projects={projects} onTrust={onTrust} onActivate={onActivate} />
        </div>
      </Flex>
    </Layout>
  );
}
