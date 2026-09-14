/** 一轮跑完之后, 消息底下那排用量。
 *
 * 一格一个胶囊: 这排数字是拿来扫的, 不是拿来读的 —— 排成一句话之后没人看得出
 * 哪个是输入哪个是输出。
 *
 * 四个 token 胶囊互不相交, 加起来就是合计 (lib/run/metrics 已经把 cached 从 input,
 * reasoning 从 output 里减出来了)。早先并排的是供应商原始的四个数, 两个子集混在里面,
 * 读的人怎么加都对不上合计。
 */

import { Tooltip } from "antd";
import { formatElapsed, formatTokens } from "@/shared/format";
import type { metricsFor } from "@/shared/lib/run/metrics";

export function FinalMetrics({ metrics }: { metrics: ReturnType<typeof metricsFor> }) {
  const chips: Array<[string, string]> = [
    ["耗时", formatElapsed(metrics.elapsedMs)],
    ["输入", formatTokens(metrics.freshInputTokens)],
  ];
  if (metrics.cachedTokens > 0) chips.push(["缓存命中", formatTokens(metrics.cachedTokens)]);
  chips.push(["输出", formatTokens(metrics.plainOutputTokens)]);
  if (metrics.reasoningTokens > 0) chips.push(["思考", formatTokens(metrics.reasoningTokens)]);
  chips.push(["模型", `${metrics.modelCalls} 次`], ["工具", `${metrics.toolCalls} 次`]);
  return (
    <span style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "4px 6px" }}>
      {chips.map(([label, value]) => (
        <span className="metric" key={label}>
          <i>{label}</i>
          {value}
        </span>
      ))}
      <Tooltip
        title={`${metrics.totalTokens.toLocaleString()} tokens · ${metrics.estimated ? "供应商未回 usage，本轮为本地估算" : "供应商返回的用量"}`}
      >
        <span className="metric total">
          <i>合计</i>
          {metrics.estimated ? "≈" : ""}
          {formatTokens(metrics.totalTokens)} tokens
        </span>
      </Tooltip>
      {/* 压缩是 Forge 自己发起的调用, 不是用户这句话直接引起的 —— 它已经在合计里,
          所以排在合计后面并标"其中", 不再和前面的加项混在一起 (ADR-0037)。 */}
      {metrics.compactTokens > 0 && (
        <span className="metric">
          <i>其中压缩</i>
          {formatTokens(metrics.compactTokens)}
        </span>
      )}
    </span>
  );
}
