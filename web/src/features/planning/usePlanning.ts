/** 计划与待办: 当前计划, 计划目录, 以及回合边界的计划评审决议。 */

import { useCallback, useState } from "react";
import { api } from "@/shared/api/client";
import type { PlanIndexView, Planning } from "@/types/session";

export function usePlanning(onError: (message: string) => void) {
  const [planning, setPlanning] = useState<Planning>({});
  const [index, setIndex] = useState<PlanIndexView | null>(null);

  const load = useCallback(async () => {
    setPlanning(await api<Planning>("/planning"));
    // 目录是次要信息: 拉不回来不该让当前计划一起显示不出来。
    api<PlanIndexView>("/plans")
      .then(setIndex)
      .catch(() => undefined);
  }, []);

  const write = useCallback(
    async (run: () => Promise<unknown>) => {
      try {
        await run();
        await load();
      } catch (reason) {
        onError((reason as Error).message);
      }
    },
    [load, onError],
  );

  return {
    planning,
    index,
    load,
    resolveReview: (decision: string) =>
      write(() =>
        api("/plan-reviews/current/resolve", {
          method: "POST",
          body: JSON.stringify({ decision, note: "" }),
        }),
      ),
    activatePlan: (planId: string) =>
      write(() => api(`/plans/${encodeURIComponent(planId)}/activate`, { method: "POST" })),
  };
}
