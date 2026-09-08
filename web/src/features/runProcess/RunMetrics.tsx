/** 处理过程底部那一行用量。 */

import type { metricsFor } from "@/shared/lib/run/metrics";
import { formatTokens } from "@/shared/format";
import { formatElapsed } from "@/shared/format";

export function RunMetrics({ metrics }: { metrics: ReturnType<typeof metricsFor> }) {
  return (
    <span className="run-metrics">
      <span>{formatElapsed(metrics.elapsedMs)}</span>
      <span>{metrics.modelCalls} 次模型</span>
      <span>{metrics.toolCalls} 次工具</span>
      <span title={tokenBreakdown(metrics)}>
        {metrics.estimated ? "约 " : ""}
        {formatTokens(metrics.totalTokens)} tokens
      </span>
    </span>
  );
}

/** 悬停才看的明细给整数: 这一栏正是用来核对上面那个 k 是怎么来的。 */
function tokenBreakdown(metrics: ReturnType<typeof metricsFor>) {
  const parts = [
    `输入 ${metrics.inputTokens}`,
    `输出 ${metrics.outputTokens}`,
    `思考 ${metrics.reasoningTokens}`,
    `缓存 ${metrics.cachedTokens}`,
  ];
  if (metrics.compactTokens > 0) parts.push(`其中上下文压缩 ${metrics.compactTokens}`);
  return parts.join(" · ");
}
