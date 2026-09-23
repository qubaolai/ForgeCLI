/** 事件流上的一条, 以及从它的 payload 里安全取值。
 *
 * 这一层不认识 turn, 也不认识时间线 —— 它只回答"这条事件长什么样"。
 */

export type RunEvent = {
  event_id: string;
  kind: string;
  /** 这条事件属于哪个会话。turn_id 在每个会话里各自从 1 起, 单独用它定位不了一轮 (ADR-0048 决策 2)。 */
  session_id: string;
  turn_id: string;
  agent_id?: string;
  sequence: number;
  occurred_at?: number;
  step_index?: number | null;
  request_id?: string | null;
  tool_call_id?: string | null;
  invocation_id?: string | null;
  payload: Record<string, unknown>;
};

/** 后端按 turn 归档的运行事件；流式增量已折叠成每次模型调用的一段正文。 */
export type RunSnapshot = {
  session_id?: string;
  turn_id: string;
  agent_id?: string;
  events: RunEvent[];
  outputs: Record<string, string>;
};

export function stringValue(value: unknown) {
  return value == null ? "" : String(value);
}

export function numberValue(value: unknown) {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

/** 同一次模型调用的分组键；模型事件与其正文必须落在同一个桶里。 */
export function modelKey(event: RunEvent) {
  return event.request_id ?? `call-${stringValue(event.payload.call_index) || "0"}`;
}

const terminalKinds = new Set(["turn_completed", "turn_failed", "turn_cancelled"]);

export function isTerminalEvent(event: RunEvent) {
  return !event.agent_id && terminalKinds.has(event.kind);
}
