/** 项目选择与信任新目录的浮层。 */

import { Modal, Typography } from "antd";
import { ProjectGrid } from "@/features/projects/ProjectGrid";
import type { Project } from "@/types/session";

export function ProjectPicker({
  projects,
  activeProjectId,
  busy,
  trustPath,
  onTrustPath,
  onTrust,
  onActivate,
  onCancel,
}: {
  projects: Project[];
  activeProjectId: string | null;
  /** 有请求在跑时不许切: 切过去之后那一轮的事件没有地方可去。 */
  busy: boolean;
  trustPath: string;
  onTrustPath: (path: string) => void;
  onTrust: () => void;
  onActivate: (id: string) => void;
  onCancel: () => void;
}) {
  return (
    <Modal open title="选择项目" onCancel={onCancel} footer={null} width={960} destroyOnHidden>
      <Typography.Paragraph type="secondary">
        {busy ? "当前请求处理中，暂不能切换项目" : "选择一个已信任项目，或添加本机目录"}
      </Typography.Paragraph>
      <ProjectGrid
        projects={projects}
        activeProjectId={activeProjectId}
        busy={busy}
        trustPath={trustPath}
        onTrustPath={onTrustPath}
        onTrust={onTrust}
        onActivate={onActivate}
      />
    </Modal>
  );
}
