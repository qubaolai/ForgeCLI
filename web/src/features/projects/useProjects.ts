/** 受信项目: 列举, 激活, 信任一个新目录。 */

import { useCallback, useMemo, useState } from "react";
import { api } from "@/shared/api/client";
import type { Project } from "@/types/session";

export function useProjects() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);

  const load = useCallback(async () => {
    const result = await api<{ items: Project[]; active_project_id: string | null }>("/projects");
    setProjects(result.items);
    setActiveProjectId(result.active_project_id);
  }, []);

  /** 只让服务端切项目, 不动界面状态。
   *
   * 单独拆出来是给"先切项目再恢复会话"用的: 那条路要等会话也切好了, 再让
   * activeProjectId 变 —— 否则界面按项目刚切完那一刻去拉工作区, 拿到的是新项目
   * 自动开的那个空会话, 随后才恢复的会话会被它盖掉。
   */
  const switchProject = useCallback(async (projectId: string) => {
    await api(`/projects/${projectId}/activate`, { method: "POST" });
  }, []);

  const activate = useCallback(
    async (projectId: string) => {
      await switchProject(projectId);
      await load();
    },
    [switchProject, load],
  );

  const trust = useCallback(
    async (path: string) => {
      const project = await api<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({ path }),
      });
      await load();
      await activate(project.project_id);
    },
    [load, activate],
  );

  const activeProject = useMemo(
    () => projects.find((item) => item.project_id === activeProjectId),
    [projects, activeProjectId],
  );

  return {
    projects,
    activeProjectId,
    activeProject,
    load,
    switchProject,
    activate,
    trust,
  };
}
