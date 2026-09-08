/** 时间线上的一条: 历史正文, 用户消息, 以及一轮的过程与用量。 */

import { formatElapsed } from "@/shared/format";
import { CopyButton } from "@/shared/ui/CopyButton";
import { Markdown } from "@/shared/ui/Markdown";
import { RunProcess } from "@/features/runProcess/RunProcess";
import { formatTokens } from "@/shared/format";
import { metricsFor } from "@/shared/lib/run/metrics";
import type { ToolDirectory } from "@/shared/lib/run/tools";
import type { LocalTurn } from "@/shared/lib/run/turn";
import type { TranscriptEvent } from "@/types/session";

export function Message({ item }: { item: TranscriptEvent }) {
  const user = item.payload.role === "user";
  const text = item.payload.text ?? "";
  if (user) return <UserMessage text={text} />;
  return (
    <article className="message assistant">
      <div className="avatar">F</div>
      <div>
        <strong>Forge</strong>
        <Markdown content={text} />
        <div className="message-footer">
          <span />
          <CopyButton content={text} className="message-copy-outside" />
        </div>
      </div>
    </article>
  );
}

/** 历史消息和刚发送的本地消息共用同一个操作区，避免复制能力只在刷新后才出现。 */
export function UserMessage({ text }: { text: string }) {
  return (
    <article className="message user">
      <div className="avatar">你</div>
      <div>
        <strong>你</strong>
        <p>{text}</p>
        <div className="message-footer">
          <span />
          <CopyButton content={text} className="message-copy-outside" />
        </div>
      </div>
    </article>
  );
}

/** 历史轮次: 与 LocalTurnView 同一个形状, 只是数据来自落盘的过程快照。 */
export function RestoredTurnView({
  run,
  item,
  directory,
}: {
  run: LocalTurn;
  item: TranscriptEvent;
  directory: ToolDirectory;
}) {
  const text = item.payload.text ?? "";
  return (
    <article className="message assistant">
      <div className="avatar">F</div>
      <div>
        <strong>Forge</strong>
        <RunProcess turn={run} directory={directory} />
        <div className="message-footer">
          <span />
          {text && <CopyButton content={text} className="message-copy-outside" />}
        </div>
      </div>
    </article>
  );
}

export function LocalTurnView({ turn, directory }: { turn: LocalTurn; directory: ToolDirectory }) {
  const metrics = metricsFor(turn.events, Date.now(), turn.startedAt);
  // 最终回答**不再单独渲染一段**: 它是 RunProcess 里最后一块叙述。两边都画一遍的话,
  // 同一段文字会出现两次 —— 而这正是把叙述从折叠块里放出来之后的必然结果。
  //
  // 只剩 error 要单独说: 它不来自任何一次模型调用, 时间线里没有它的位置。
  //
  // 占位轮次没有本地用户文本（刷新页面后接上的 turn），用户消息已经在 transcript 里。
  return (
    <section className="local-turn">
      {turn.userText && <UserMessage text={turn.userText} />}
      <article className="message assistant">
        <div className="avatar">F</div>
        <div>
          <strong>Forge</strong>
          <RunProcess turn={turn} directory={directory} />
          {turn.error && <p className="run-flow-error">{turn.error}</p>}
          <div className="message-footer">
            {turn.status !== "running" ? <FinalMetrics metrics={metrics} /> : <span />}
            {turn.assistantText && (
              <CopyButton content={turn.assistantText} className="message-copy-outside" />
            )}
          </div>
        </div>
      </article>
    </section>
  );
}

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
