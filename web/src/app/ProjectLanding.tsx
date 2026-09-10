/** 还没有活动项目时的整屏落地页: 选一个已信任的项目, 或者信任一个新目录。 */

import { Avatar, Flex, Layout, Space, Typography, theme } from "antd";
import { ProjectGrid } from "@/features/projects/ProjectGrid";
import type { Project } from "@/types/session";

export function ProjectLanding({
  projects,
  trustPath,
  onTrustPath,
  onActivate,
  onTrust,
}: {
  projects: Project[];
  trustPath: string;
  onTrustPath: (path: string) => void;
  onActivate: (projectId: string) => void;
  onTrust: () => void;
}) {
  const { token } = theme.useToken();
  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Flex vertical align="center" justify="center" gap="large" style={{ padding: 32 }}>
        <Space size="middle">
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
        <div style={{ width: "100%", maxWidth: 960 }}>
          <ProjectGrid
            projects={projects}
            trustPath={trustPath}
            onTrustPath={onTrustPath}
            onTrust={onTrust}
            onActivate={onActivate}
          />
        </div>
      </Flex>
    </Layout>
  );
}
