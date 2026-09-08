/** 一轮跑完之后, 消息底下那排用量。 */

import { formatElapsed, formatTokens } from "@/shared/format";
import type { metricsFor } from "@/shared/lib/run/metrics";

export function FinalMetrics({ metrics }: { metrics: ReturnType<typeof metricsFor> }) {
  const chips: Array<[string, string]> = [
    ["耗时", formatElapsed(metrics.elapsedMs)],
    ["输入", formatTokens(metrics.inputTokens)],
    ["输出", formatTokens(metrics.outputTokens)],
  ];
  if (metrics.reasoningTokens > 0) chips.push(["思考", formatTokens(metrics.reasoningTokens)]);
  if (metrics.cachedTokens > 0) chips.push(["缓存", formatTokens(metrics.cachedTokens)]);
  // 压缩是 Forge 自己发起的调用, 不是用户这句话直接引起的 —— 单列一格, 但仍然算进合计。
  if (metrics.compactTokens > 0) chips.push(["压缩", formatTokens(metrics.compactTokens)]);
  chips.push(["模型", `${metrics.modelCalls} 次`], ["工具", `${metrics.toolCalls} 次`]);
  return (
    <span className="final-metrics">
      {chips.map(([label, value]) => (
        <span className="metric" key={label}>
          <i>{label}</i>
          {value}
        </span>
      ))}
      <span
        className="metric total"
        title={`${metrics.totalTokens.toLocaleString()} tokens · ${metrics.estimated ? "供应商未回 usage，本轮为本地估算" : "供应商返回的用量"}`}
      >
        <i>合计</i>
        {metrics.estimated ? "≈" : ""}
        {formatTokens(metrics.totalTokens)} tokens
      </span>
    </span>
  );
}
