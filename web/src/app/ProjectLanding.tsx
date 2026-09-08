/** 还没有活动项目时的整屏落地页: 选一个已信任的项目, 或者信任一个新目录。 */

import type { FormEvent } from "react";
import { shortPath } from "@/shared/format";
import type { Project } from "@/types/session";

export function ProjectLanding({
  projects,
  error,
  trustPath,
  onTrustPath,
  onActivate,
  onTrust,
}: {
  projects: Project[];
  error: string;
  trustPath: string;
  onTrustPath: (path: string) => void;
  onActivate: (projectId: string) => void;
  onTrust: (event: FormEvent) => void;
}) {
  return (
    <main className="project-center">
      <div className="brand-lockup">
        <span className="forge-mark">F</span>
        <div>
          <h1>Forge</h1>
          <p>本地优先的 AI 工程工作台</p>
        </div>
      </div>
      {error && <div className="error-banner">{error}</div>}
      <section className="project-grid">
        {projects.map((project) => (
          <button
            className="project-card"
            key={project.project_id}
            onClick={() => onActivate(project.project_id)}
          >
            <span className="project-icon">⌘</span>
            <strong>{shortPath(project.primary_workspace_root)}</strong>
            <small>{project.primary_workspace_root}</small>
            <span>打开项目 →</span>
          </button>
        ))}
        <form className="project-card add-project" onSubmit={onTrust}>
          <strong>信任新项目</strong>
          <small>输入本机目录的绝对路径</small>
          <input
            value={trustPath}
            onChange={(event) => onTrustPath(event.target.value)}
            placeholder="/path/to/repository"
          />
          <button type="submit">添加并打开</button>
        </form>
      </section>
    </main>
  );
}
