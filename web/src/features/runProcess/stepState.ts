/** 一个步骤的四种样子, 以及怎么从事件里读出来。
 *
 * 与渲染分开: 这几条是判断, 不是画法 —— 而判断错了 (比如把已经取消的工具显示成
 * "进行中") 光看截图是看不出来的。
 */

import type { RunEvent } from "@/shared/lib/run/events";
import { stringValue } from "@/shared/lib/run/events";
import type { LocalTurn } from "@/shared/lib/run/turn";

export type StepState = "pending" | "running" | "done" | "failed" | "cancelled";

export function stateOfTool(events: RunEvent[], turnStatus: LocalTurn["status"]): StepState {
  const completed = [...events]
    .reverse()
    .find((event) => event.kind === "tool_completed" || event.kind === "tool_cancelled");
  const started = events.some((event) => event.kind === "tool_started");
  return settle(
    completed
      ? completed.kind === "tool_cancelled"
        ? "cancelled"
        : completed.payload.error_summary
          ? "failed"
          : "done"
      : started
        ? "running"
        : "pending",
    turnStatus,
  );
}

/** turn 已经结束时，没有终态事件的步骤不能继续显示成"进行中"。 */
function settle(state: StepState, turnStatus: LocalTurn["status"]): StepState {
  if ((state !== "running" && state !== "pending") || turnStatus === "running") return state;
  if (turnStatus === "cancelled") return "cancelled";
  return turnStatus === "failed" ? "failed" : "done";
}

export function statusText(status: LocalTurn["status"]) {
  if (status === "running") return "Forge 正在处理";
  if (status === "completed") return "处理过程";
  if (status === "cancelled") return "处理已取消";
  return "处理失败";
}

export function toolState(
  latest: RunEvent | undefined,
  completed: RunEvent | undefined,
  awaiting: boolean,
  state: StepState,
) {
  if (completed) {
    const status = stringValue(completed.payload.status);
    const labels: Record<string, string> = {
      ok: "完成",
      not_run: "未执行",
      rejected: "已拒绝",
      cancelled: "已取消",
    };
    return (labels[status] ?? status) || "完成";
  }
  if (state === "pending" && latest?.kind === "tool_queued") return "待处理";
  if (state !== "running" && state !== "pending") return "未完成";
  if (awaiting) return "等待审批";
  // 执行中的工具名称本身会闪烁，不再额外摆“执行中”状态标记。
  const labels: Record<string, string> = {
    tool_queued: "排队",
    tool_prepared: "已准备",
    policy_resolved: "安全检查",
    approval_resolved: "审批完成",
    tool_started: "",
  };
  return latest ? (labels[latest.kind] ?? "处理中") : "处理中";
}
