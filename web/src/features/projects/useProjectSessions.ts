import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/shared/api/client";
import type { Project, Session } from "@/types/session";

export const PROJECT_SESSION_PAGE_SIZE = 5;

export type ProjectSessionGroup = {
  items: Session[];
  hasMore: boolean;
  nextOffset: number | null;
  loading: boolean;
};

type ProjectSessions = Record<string, ProjectSessionGroup>;

type ProjectSessionsPage = {
  items: Session[];
  has_more: boolean;
  next_offset: number | null;
};

const emptyGroup = (): ProjectSessionGroup => ({
  items: [],
  hasMore: false,
  nextOffset: null,
  loading: false,
});

/** 只读取展开项目的会话；其他项目在用户展开时加载。 */
export function useProjectSessions(
  projects: Project[],
  activeProjectId: string | null,
  onError: (message: string) => void,
) {
  const [groups, setGroups] = useState<ProjectSessions>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const visible = useRef(new Set<string>());
  const pending = useRef(new Map<string, Promise<void>>());
  const latestGroups = useRef<ProjectSessions>({});
  // 用项目 id 而不是 projects 数组引用作为依赖，项目元数据刷新不会重拉会话。
  const projectKey = JSON.stringify(projects.map((project) => project.project_id));
  const expandedProjects = useMemo(
    () => (JSON.parse(projectKey) as string[]).filter((id) => expanded[id] ?? id === activeProjectId),
    [projectKey, expanded, activeProjectId],
  );

  const loadPage = useCallback(
    (projectId: string, append = false): Promise<void> => {
      const existing = pending.current.get(projectId);
      if (existing) return existing;
      const current = latestGroups.current[projectId] ?? emptyGroup();
      if (append && (!current.hasMore || current.nextOffset === null)) return Promise.resolve();
      const update = (group: ProjectSessionGroup) => {
        latestGroups.current = { ...latestGroups.current, [projectId]: group };
        setGroups(latestGroups.current);
      };
      update({ ...current, loading: true });
      const request = (async () => {
        try {
          const result = await api<ProjectSessionsPage>(
            `/projects/${encodeURIComponent(projectId)}/sessions?offset=${append ? current.nextOffset : 0}&limit=${PROJECT_SESSION_PAGE_SIZE}`,
          );
          update({
            items: append ? [...current.items, ...result.items] : result.items,
            hasMore: result.has_more,
            nextOffset: result.next_offset,
            loading: false,
          });
        } catch (reason) {
          update({ ...current, loading: false });
          onError((reason as Error).message);
        } finally {
          pending.current.delete(projectId);
        }
      })();
      pending.current.set(projectId, request);
      return request;
    },
    [onError],
  );

  const load = useCallback(async () => {
    await Promise.all(expandedProjects.map((id) => loadPage(id)));
  }, [expandedProjects, loadPage]);

  useEffect(() => {
    for (const id of expandedProjects) {
      if (!visible.current.has(id)) void loadPage(id);
    }
    visible.current = new Set(expandedProjects);
  }, [expandedProjects, loadPage]);

  const toggleProject = useCallback(
    (projectId: string) => {
      setExpanded((items) => ({
        ...items,
        [projectId]: !(items[projectId] ?? projectId === activeProjectId),
      }));
    },
    [activeProjectId],
  );

  return {
    groups,
    expandedProjects,
    toggleProject,
    load,
    loadNextPage: (projectId: string) => loadPage(projectId, true),
  };
}
