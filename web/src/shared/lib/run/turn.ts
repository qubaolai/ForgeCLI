/** 一轮 (turn) 的本地状态, 以及把事件折进去的那几个纯变换。
 *
 * 全部是纯函数: 同样的输入必须得到同样的输出。StrictMode 会拿同一份输入把
 * updater 跑两遍, 靠副作用记账的写法在那里必然出错。
 */

import type { RunEvent, RunSnapshot } from "@/shared/lib/run/events";
import { isTerminalEvent, modelKey, numberValue, stringValue } from "@/shared/lib/run/events";

export type LocalTurnStatus = "running" | "completed" | "failed" | "cancelled";

export type LocalTurn = {
  clientId: string;
  turnId: string | null;
  userText: string;
  /**
   * **已确认是最终回答**的那段正文。模型表态之前它一直是空的。
   *
   * 流式增量不进这里：一次以工具调用收尾的模型调用, 前面那段"我先看看文件"是过程而
   * 不是答案。先塞进回答区再收回去, 用户看到的就是一段话在聊天里闪一下又消失 ——
   * 而 outputs 里同一段文字这时又出现在处理过程里, 像是跳过去又跳回来。
   */
  assistantText: string;
  /** 被认定为最终回答的那次模型调用；在它出现之前, 正文只属于处理过程。 */
  answerRequestId: string | null;
  lastRequestId: string | null;
  /**
   * 结构性事件；**不含** `model_output_delta`。
   *
   * 一次长回答会推来上千条增量，把它们留在数组里，每条都要复制、排序、查重一遍，
   * 渲染开销就成了事件数的平方 —— 页面卡住就是这么来的。正文按调用存进 outputs。
   */
  events: RunEvent[];
  /** request_id -> 该次模型调用累积的可见正文。 */
  outputs: Record<string, string>;
  /** 本 turn 已处理到的事件序号；总线保证 turn 内单调递增，用它做 O(1) 去重。 */
  lastSequence: number;
  status: LocalTurnStatus;
  startedAt: number;
  error: string;
};

export function newLocalTurn(userText: string, now = Date.now()): LocalTurn {
  return {
    clientId: `local-${now}-${Math.random().toString(36).slice(2, 8)}`,
    turnId: null,
    userText,
    assistantText: "",
    answerRequestId: null,
    lastRequestId: null,
    events: [],
    outputs: {},
    lastSequence: 0,
    status: "running",
    startedAt: now,
    error: "",
  };
}

export function appendRunEvent(turns: LocalTurn[], event: RunEvent, now = Date.now()): LocalTurn[] {
  let source = turns;
  let index = source.findIndex((turn) => turn.turnId === event.turn_id);
  if (index < 0) {
    index = source.findIndex((turn) => turn.status === "running" && !turn.turnId);
  }
  if (index < 0) {
    // 刷新页面后重新接上仍在运行的 turn：补一个占位轮次，事件不再被丢弃。
    source = [...turns, { ...newLocalTurn("", now), turnId: event.turn_id }];
    index = source.length - 1;
  }

  const current = source[index];
  // 纯函数式去重：StrictMode 会拿同样的输入把 updater 跑两遍，不能靠副作用记账。
  if (event.sequence <= current.lastSequence) return turns;

  let assistantText = current.assistantText;
  let answerRequestId = current.answerRequestId;
  let lastRequestId = current.lastRequestId;
  let outputs = current.outputs;
  if (event.kind === "model_output_delta") {
    const delta = stringValue(event.payload.text);
    const requestId = modelKey(event);
    if (!event.agent_id) lastRequestId = requestId;
    outputs = { ...outputs, [requestId]: (outputs[requestId] ?? "") + delta };
    // 已经认定是最终回答的那次调用还在吐字, 正文跟着长。其余调用只进 outputs。
    if (answerRequestId === requestId) assistantText = outputs[requestId];
  }
  // 模型这次没再要工具, 它说的就是最终回答: 到这一刻才交给回答区, 而且只交一次。
  if (
    !event.agent_id &&
    event.kind === "model_completed" &&
    numberValue(event.payload.tool_call_count) === 0
  ) {
    answerRequestId = event.request_id ?? lastRequestId;
    if (answerRequestId) assistantText = outputs[answerRequestId] ?? "";
  }

  let status = current.status;
  if (!event.agent_id && event.kind === "turn_completed") status = "completed";
  if (!event.agent_id && event.kind === "turn_failed") status = "failed";
  if (!event.agent_id && event.kind === "turn_cancelled") status = "cancelled";

  const next = [...source];
  next[index] = {
    ...current,
    turnId: current.turnId ?? event.turn_id,
    assistantText,
    answerRequestId,
    lastRequestId,
    outputs,
    events: event.kind === "model_output_delta" ? current.events : [...current.events, event],
    lastSequence: event.sequence,
    status,
  };
  return next;
}

/** 用后端的归档快照重建一个只读的处理过程。 */
export function restoreTurn(snapshot: RunSnapshot, now = Date.now()): LocalTurn {
  const events = [...(snapshot.events ?? [])].sort((a, b) => a.sequence - b.sequence);
  const terminal = [...events].reverse().find((event) => isTerminalEvent(event));
  // 回答本身已经在 transcript 里了, 这里只要知道**哪次调用**是回答 —— 处理过程据此
  // 不再重复展示那一段, 否则刷新之后同一段文字会出现两遍。
  const answer = [...events].reverse().find(isAnswerCall);
  return {
    clientId: `restored-${snapshot.turn_id}`,
    turnId: snapshot.turn_id,
    userText: "",
    assistantText: "",
    answerRequestId: answer?.request_id ?? null,
    lastRequestId: null,
    events,
    outputs: snapshot.outputs ?? {},
    lastSequence: events.at(-1)?.sequence ?? 0,
    status: restoredStatus(terminal),
    startedAt: now,
    error: "",
  };
}

function isAnswerCall(event: RunEvent) {
  return (
    !event.agent_id && event.kind === "model_completed" && numberValue(event.payload.tool_call_count) === 0
  );
}

function restoredStatus(terminal?: RunEvent): LocalTurnStatus {
  if (!terminal) return "running";
  if (terminal.kind === "turn_failed") return "failed";
  if (terminal.kind === "turn_cancelled") return "cancelled";
  return "completed";
}

export function finishLocalTurn(
  turns: LocalTurn[],
  turnId: string,
  responseText?: string,
  error = "",
): LocalTurn[] {
  return turns.map((turn) => {
    if (turn.turnId !== turnId) return turn;
    return {
      ...turn,
      assistantText: responseText?.trim() ? responseText : turn.assistantText,
      error: error || turn.error,
      status: error ? "failed" : turn.status,
    };
  });
}

export function failUnboundTurn(turns: LocalTurn[], clientId: string, error: string): LocalTurn[] {
  return turns.map((turn) => (turn.clientId === clientId ? { ...turn, status: "failed", error } : turn));
}

/**
 * 一轮失败时给人的安全错误摘要。
 *
 * 后端已经把供应商响应归一化、截断后放进 `turn_failed.detail`；这里不再猜异常类型，也不
 * 回退到原始响应体。取最后一条是为了兼容事件重放以及未来可能增加的阶段性失败事件。
 */
export function terminalFailureDetail(events: RunEvent[]) {
  const failed = [...events].reverse().find((event) => event.kind === "turn_failed");
  return stringValue(failed?.payload.detail);
}
