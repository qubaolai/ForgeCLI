/** 切项目与信任新目录时, 界面这一侧要跟着做的事。
 *
 * useProjects 只管后端那一半 (列举, 激活, 信任)。切换还要清掉上一个项目的正文, 关掉
 * 选择器 —— 而且在有请求在跑时直接拒绝: 切过去之后那一轮的事件没有地方可去。
 */

import type { useProjects } from "@/features/projects/useProjects";

const BUSY_MESSAGE = "当前请求仍在处理，请先等待完成或停止后再切换项目。";

export function useProjectSwitch({
  projects,
  busy,
  clearTimeline,
  closePicker,
  resume,
  onError,
}: {
  projects: ReturnType<typeof useProjects>;
  busy: boolean;
  clearTimeline: () => void;
  closePicker: () => void;
  /** 恢复当前项目下的一个会话 (useSessionActions.resume)。 */
  resume: (sessionId: string) => Promise<void>;
  onError: (message: string) => void;
}) {
  async function activate(projectId: string) {
    if (busy) {
      onError(BUSY_MESSAGE);
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

  /** 打开某个项目下的会话: 是当前项目就直接恢复, 不是就先切项目再恢复。
   *
   * 顺序是关键: 服务端切项目 -> 服务端切会话 -> 最后才让 activeProjectId 变。
   * activeProjectId 一变, 界面就会重连事件流并按服务端状态重拉工作区; 那时服务端
   * 已经停在要恢复的会话上, 拉回来的就是它。反过来先让界面知道切了项目, 它会先拿到
   * 新项目自动开的空会话, 再被稍后的恢复盖一次 —— 两个请求谁后到谁赢。
   *
   * 中间哪一步失败, 界面都要追上服务端实际停在哪, 所以 load 放 finally。
   */
  async function openSession(projectId: string, sessionId: string) {
    if (projectId === projects.activeProjectId) {
      await resume(sessionId);
      return;
    }
    if (busy) {
      onError(BUSY_MESSAGE);
      return;
    }
    onError("");
    clearTimeline();
    closePicker();
    try {
      await projects.switchProject(projectId);
      await resume(sessionId);
    } catch (reason) {
      onError((reason as Error).message);
    } finally {
      await projects.load().catch((reason: Error) => onError(reason.message));
    }
  }

  async function trust(path: string) {
    clearTimeline();
    closePicker();
    try {
      await projects.trust(path);
    } catch (reason) {
      onError((reason as Error).message);
    }
  }

  return { activate, trust, openSession };
}
