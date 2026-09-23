/** 一轮烧了多少: 耗时, 调用次数, token。 */

import type { RunEvent } from "@/shared/lib/run/events";
import { isTerminalEvent, numberValue, stringValue } from "@/shared/lib/run/events";

export type TurnMetrics = {
  elapsedMs: number;
  modelCalls: number;
  toolCalls: number;
  // 下面四项互不相交, 加起来就是 totalTokens。供应商回的 input 含 cached, output 含
  // reasoning (口径定在后端 domain/model/response.py 的 ModelUsage); 这里先减出来 ——
  // 早先四个数原样并排, 读的人把两个子集也当成加项, 于是怎么加都对不上合计。
  freshInputTokens: number;
  cachedTokens: number;
  plainOutputTokens: number;
  reasoningTokens: number;
  totalTokens: number;
  // 合计里属于上下文压缩的那部分。压缩是 Forge 自己发起的额外模型调用, 混在总数里
  // 不标出来, 用户拿总数去核账单时会发现一次对不上的调用 (ADR-0037)。
  compactTokens: number;
  estimated: boolean;
};

export function metricsFor(events: RunEvent[], now = Date.now(), startedAt = now): TurnMetrics {
  const usage = events.filter((event) => event.kind === "model_usage");
  const compaction = usage.filter((event) => isCompactionUsage(event));
  const finished = [...events].reverse().find((event) => isTerminalEvent(event));
  const payload = finished?.payload ?? {};
  const input = sumPayload(usage, "input_tokens");
  const output = sumPayload(usage, "output_tokens");
  const cached = Math.min(sumPayload(usage, "cached_tokens"), input);
  const reasoning = Math.min(sumPayload(usage, "reasoning_tokens"), output);
  return {
    elapsedMs: numberValue(payload.elapsed_ms) || Math.max(0, now - startedAt),
    modelCalls:
      events.filter((event) => event.kind === "model_started").length || numberValue(payload.model_calls),
    toolCalls: uniqueToolCount(events) || numberValue(payload.tool_calls),
    freshInputTokens: input - cached,
    cachedTokens: cached,
    plainOutputTokens: output - reasoning,
    reasoningTokens: reasoning,
    totalTokens: sumTotal(usage),
    compactTokens: sumTotal(compaction),
    estimated: usage.some((event) => Boolean(event.payload.estimated)),
  };
}

/** 供应商回了 total 就用它, 没回就按 input + output 补 —— 与后端 ModelUsage 的口径一致。 */
function sumTotal(events: RunEvent[]) {
  return events.reduce((total, event) => {
    const explicit = numberValue(event.payload.total_tokens);
    return (
      total + (explicit || numberValue(event.payload.input_tokens) + numberValue(event.payload.output_tokens))
    );
  }, 0);
}

/** 这条用量属于上下文压缩, 不属于 Agent 自己的某次模型调用。 */
export function isCompactionUsage(event: RunEvent) {
  return event.kind === "model_usage" && stringValue(event.payload.origin) === "compact";
}

function sumPayload(events: RunEvent[], key: string) {
  return events.reduce((total, event) => total + numberValue(event.payload[key]), 0);
}

function uniqueToolCount(events: RunEvent[]) {
  return new Set(
    events
      .filter((event) => event.kind === "tool_queued")
      .map(
        (event) => `${event.agent_id ?? ""}:${event.invocation_id ?? event.tool_call_id ?? event.event_id}`,
      ),
  ).size;
}

// ---- 处理过程的步骤时间线 ----
