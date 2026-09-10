/** 处理过程底部那一行用量。跑的过程中它一直在变, 所以只给量级, 明细挂在悬停上。 */

import { Space, Tooltip, Typography } from "antd";
import { formatElapsed, formatTokens } from "@/shared/format";
import type { metricsFor } from "@/shared/lib/run/metrics";

export function RunMetrics({ metrics }: { metrics: ReturnType<typeof metricsFor> }) {
  return (
    <Space size={5} wrap split="·" style={{ fontSize: 10 }}>
      <Typography.Text type="secondary" style={{ fontSize: 10 }}>
        {formatElapsed(metrics.elapsedMs)}
      </Typography.Text>
      <Typography.Text type="secondary" style={{ fontSize: 10 }}>
        {metrics.modelCalls} 次模型
      </Typography.Text>
      <Typography.Text type="secondary" style={{ fontSize: 10 }}>
        {metrics.toolCalls} 次工具
      </Typography.Text>
      <Tooltip title={tokenBreakdown(metrics)}>
        <Typography.Text type="secondary" style={{ fontSize: 10 }}>
          {metrics.estimated ? "约 " : ""}
          {formatTokens(metrics.totalTokens)} tokens
        </Typography.Text>
      </Tooltip>
    </Space>
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
