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

/** 左侧项目导航的会话摘要：按项目读取，每次只展开一页。 */
export function useProjectSessions(projects: Project[], onError: (message: string) => void) {
  const [groups, setGroups] = useState<ProjectSessions>({});
  const requestVersion = useRef(0);
  const projectIds = useMemo(() => projects.map((project) => project.project_id), [projects]);
  const projectKey = projectIds.join(",");

  const load = useCallback(async () => {
    const version = ++requestVersion.current;
    const results = await Promise.all(
      projectIds.map(async (projectId) => {
        try {
          const result = await api<ProjectSessionsPage>(
            `/projects/${encodeURIComponent(projectId)}/sessions?offset=0&limit=${PROJECT_SESSION_PAGE_SIZE}`,
          );
          return [
            projectId,
            {
              items: result.items,
              hasMore: result.has_more,
              nextOffset: result.next_offset,
              loading: false,
            },
          ] as const;
        } catch (reason) {
          onError((reason as Error).message);
          return [projectId, emptyGroup()] as const;
        }
      }),
    );
    if (version !== requestVersion.current) return;
    setGroups(Object.fromEntries(results));
  }, [onError, projectIds]);

  useEffect(() => {
    void load();
  }, [load, projectKey]);

  const loadNextPage = useCallback(
    async (projectId: string) => {
      const current = groups[projectId];
      if (!current || !current.hasMore || current.loading || current.nextOffset === null) return;
      setGroups((items) => ({ ...items, [projectId]: { ...current, loading: true } }));
      try {
        const result = await api<ProjectSessionsPage>(
          `/projects/${encodeURIComponent(projectId)}/sessions?offset=${current.nextOffset}&limit=${PROJECT_SESSION_PAGE_SIZE}`,
        );
        setGroups((items) => {
          const latest = items[projectId] ?? emptyGroup();
          return {
            ...items,
            [projectId]: {
              items: [...latest.items, ...result.items],
              hasMore: result.has_more,
              nextOffset: result.next_offset,
              loading: false,
            },
          };
        });
      } catch (reason) {
        onError((reason as Error).message);
        setGroups((items) => ({ ...items, [projectId]: { ...current, loading: false } }));
      }
    },
    [groups, onError],
  );

  return { groups, load, loadNextPage };
}
