/** 折叠状态下那一行"现在在干什么"。 */

import { stringValue } from "@/shared/lib/run/events";
import type { LocalTurn } from "@/shared/lib/run/turn";
import type { ToolDirectory } from "@/shared/lib/run/tools";
import { groupToolEvents, toolActionLabel, toolNameOf } from "@/shared/lib/run/tools";

/**
 * 把一轮的事件排成给人看的步骤。
 *
 * 顺序不能反: 先按 sequence 排成一条真实时间线, **再**合并相邻同类。反过来做 —— 在
 * 只含工具的列表里判"相邻" —— 会把中间隔着一次模型调用的两次读文件也当成连续的, 于是
 * 合并出来的那一段带着更早的 sequence, 排到模型调用前面去, 时间线就说了假话。
 */
/**
 * 折叠状态下那一行"现在在干什么"。
 *
 * 处理过程默认是收起的, 所以这一行是运行期间用户唯一看得到的进度 —— 它必须说得具体,
 * "处理中"三个字等于没说。取的是**最后一件还没收尾的事**, 不是最后一条事件: 工具跑完
 * 之后紧跟着的那条 model_started 才是当下在发生的。
 */
export function activityOf(turn: LocalTurn, directory: ToolDirectory = {}): string {
  if (turn.status === "completed") return "处理完成";
  if (turn.status === "cancelled") return "处理已取消";
  if (turn.status === "failed") return "处理失败";

  const stopping = turn.events.find(
    (event) =>
      event.kind === "sub_agent_stopping" &&
      !turn.events.some((end) => end.agent_id === event.agent_id && end.kind === "sub_agent_finished"),
  );
  if (stopping) return "子任务正在停止…";
  let modelRunning = false;
  let thinking = false;
  let awaiting = false;
  let lastNote = "";

  for (const event of turn.events.filter((event) => !event.agent_id)) {
    switch (event.kind) {
      case "approval_requested":
        awaiting = true;
        break;
      case "approval_resolved":
        awaiting = false;
        break;
      case "model_started":
        modelRunning = true;
        thinking = false;
        break;
      case "model_completed":
      case "model_failed":
        modelRunning = false;
        thinking = false;
        break;
      case "model_reasoning_status":
        thinking = stringValue(event.payload.status) === "started";
        break;
      case "todo_updated":
        lastNote = "更新任务";
        break;
      case "plan_proposed":
        lastNote = "提交计划";
        break;
      case "context_compacted":
        lastNote = "压缩上下文";
        break;
      default:
        break;
    }
  }

  // 等审批排在最前: 这时候什么都没在跑, 而用户正是那个卡住流程的人。
  if (awaiting) return "等待你的审批";
  // 通过分组后的生命周期判断，避免 queued 用 tool_call_id、started/completed 用
  // invocation_id 时同一次调用在 Map 里留下两个身份。队列是串行的，不能称作“并行处理”。
  const activeTools = groupToolEvents(turn.events.filter((event) => !event.agent_id)).filter(
    (group) =>
      !group.events.some((event) => event.kind === "tool_completed" || event.kind === "tool_cancelled"),
  );
  const runningTools = activeTools.filter((group) =>
    group.events.some((event) => event.kind === "tool_started"),
  );
  if (runningTools.length > 0) {
    const names = [...new Set(runningTools.map((group) => toolNameOf(group.events)).filter(Boolean))];
    const label = names.length === 1 ? toolActionLabel(names[0], directory) : `${runningTools.length} 个工具`;
    const waiting = activeTools.length - runningTools.length;
    return `正在${label}${waiting > 0 ? `，另有 ${waiting} 个待处理` : ""}`;
  }
  if (activeTools.length > 0) {
    const names = [...new Set(activeTools.map((group) => toolNameOf(group.events)).filter(Boolean))];
    if (activeTools.length === 1 && names.length === 1)
      return `准备调用${toolActionLabel(names[0], directory)}`;
    return `准备调用 ${activeTools.length} 个工具`;
  }
  if (modelRunning) return thinking ? "模型思考中…" : "模型生成中…";
  if (lastNote) return lastNote;
  return turn.events.length ? "正在处理" : "正在建立本轮事件流…";
}
