/** 项目选择与信任新目录的整屏浮层。 */

import type { FormEvent } from "react";
import { shortPath } from "@/shared/format";
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
            <ProjectCard
              key={project.project_id}
              project={project}
              current={project.project_id === activeProjectId}
              busy={busy}
              onActivate={onActivate}
            />
          ))}
          <TrustProjectForm busy={busy} path={trustPath} onPath={onTrustPath} onSubmit={onTrust} />
        </section>
        <p className="escape-hint">按 Esc 关闭</p>
      </main>
    </div>
  );
}

function ProjectCard({
  project,
  current,
  busy,
  onActivate,
}: {
  project: Project;
  current: boolean;
  busy: boolean;
  onActivate: (id: string) => void;
}) {
  return (
    <button
      className={`project-card ${current ? "active" : ""}`}
      disabled={busy || current}
      onClick={() => onActivate(project.project_id)}
    >
      <span className="project-icon">⌘</span>
      <strong>{shortPath(project.primary_workspace_root)}</strong>
      <small>{project.primary_workspace_root}</small>
      <span>{current ? "当前项目" : "打开项目 →"}</span>
    </button>
  );
}

function TrustProjectForm({
  busy,
  path,
  onPath,
  onSubmit,
}: {
  busy: boolean;
  path: string;
  onPath: (path: string) => void;
  onSubmit: (event: FormEvent) => void;
}) {
  return (
    <form className="project-card add-project" onSubmit={onSubmit}>
      <strong>信任新项目</strong>
      <small>输入本机目录的绝对路径</small>
      <input
        value={path}
        onChange={(event) => onPath(event.target.value)}
        placeholder="/path/to/repository"
        disabled={busy}
      />
      <button type="submit" disabled={busy || !path.trim()}>
        添加并打开
      </button>
    </form>
  );
}
