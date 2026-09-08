/** 受信项目: 列举, 激活, 信任一个新目录。 */

import { useCallback, useMemo, useState } from "react";
import { api } from "../../api/client";
import type { Project } from "../../types";

export function useProjects() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);
  const [trustPath, setTrustPath] = useState("");

  const load = useCallback(async () => {
    const result = await api<{ items: Project[]; active_project_id: string | null }>("/projects");
    setProjects(result.items);
    setActiveProjectId(result.active_project_id);
  }, []);

  const activate = useCallback(
    async (projectId: string) => {
      await api(`/projects/${projectId}/activate`, { method: "POST" });
      await load();
    },
    [load],
  );

  const trust = useCallback(
    async (path: string) => {
      const project = await api<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({ path }),
      });
      setTrustPath("");
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
    trustPath,
    setTrustPath,
    load,
    activate,
    trust,
  };
}
