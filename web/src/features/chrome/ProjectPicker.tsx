/** 项目选择与信任新目录。 */

import type { FormEvent } from "react";
import type { Project } from "../../types";
import { shortPath } from "../../ui";

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
  busy: boolean;
  trustPath: string;
  onTrustPath: (path: string) => void;
  onTrust: (event: FormEvent) => void;
  onActivate: (id: string) => void;
  onCancel: () => void;
}) {
  return (
    <div className="project-switcher-layer" role="dialog" aria-modal="true" aria-label="选择项目">
      <main className="project-center project-switcher">
        <button className="project-switcher-close" onClick={onCancel} aria-label="关闭项目选择">
          ×
        </button>
        <div className="brand-lockup">
          <span className="forge-mark">F</span>
          <div>
            <h1>选择项目</h1>
            <p>{busy ? "当前请求处理中，暂不能切换项目" : "选择一个已信任项目，或添加本机目录"}</p>
          </div>
        </div>
        <section className="project-grid">
          {projects.map((project) => (
            <button
              className={`project-card ${project.project_id === activeProjectId ? "active" : ""}`}
              disabled={busy || project.project_id === activeProjectId}
              onClick={() => onActivate(project.project_id)}
              key={project.project_id}
            >
              <span className="project-icon">⌘</span>
              <strong>{shortPath(project.primary_workspace_root)}</strong>
              <small>{project.primary_workspace_root}</small>
              <span>{project.project_id === activeProjectId ? "当前项目" : "打开项目 →"}</span>
            </button>
          ))}
          <form className="project-card add-project" onSubmit={onTrust}>
            <strong>信任新项目</strong>
            <small>输入本机目录的绝对路径</small>
            <input
              value={trustPath}
              onChange={(event) => onTrustPath(event.target.value)}
              placeholder="/path/to/repository"
              disabled={busy}
            />
            <button type="submit" disabled={busy || !trustPath.trim()}>
              添加并打开
            </button>
          </form>
        </section>
        <p className="escape-hint">按 Esc 关闭</p>
      </main>
    </div>
  );
}
