/** 切项目与信任新目录时, 界面这一侧要跟着做的事。
 *
 * useProjects 只管后端那一半 (列举, 激活, 信任)。切换还要清掉上一个项目的正文, 关掉
 * 选择器 —— 而且在有请求在跑时直接拒绝: 切过去之后那一轮的事件没有地方可去。
 */

import type { FormEvent } from "react";
import type { useProjects } from "@/features/projects/useProjects";

export function useProjectSwitch({
  projects,
  busy,
  clearTimeline,
  closePicker,
  onError,
}: {
  projects: ReturnType<typeof useProjects>;
  busy: boolean;
  clearTimeline: () => void;
  closePicker: () => void;
  onError: (message: string) => void;
}) {
  async function activate(projectId: string) {
    if (busy) {
      onError("当前请求仍在处理，请先等待完成或停止后再切换项目。");
      return;
    }
    try {
      onError("");
      clearTimeline();
      closePicker();
      await projects.activate(projectId);
    } catch (reason) {
      onError((reason as Error).message);
    }
  }

  async function trust(event: FormEvent) {
    event.preventDefault();
    clearTimeline();
    closePicker();
    try {
      await projects.trust(projects.trustPath);
    } catch (reason) {
      onError((reason as Error).message);
    }
  }

  return { activate, trust };
}
