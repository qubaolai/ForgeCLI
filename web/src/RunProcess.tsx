import { MouseEvent, ReactNode, useEffect, useState } from "react";
import { ChevronIcon } from "./icons";
import { Markdown } from "./Markdown";
import {
  activityOf,
  buildTimeline,
  formatTokens,
  LocalTurn,
  metricsFor,
  numberValue,
  RunEvent,
  showsOutput,
  stringValue,
  ToolCategory,
  ToolDirectory,
  ToolGroup,
  toolActionLabel,
  toolNameOf,
  wasExecuted,
} from "./runModel";

type StepState = "running" | "done" | "failed" | "cancelled";

/** `<details>` 的 toggle 是异步事件；受控用法必须自己同步翻转，否则会被重渲染覆盖。 */
function toggle(setOpen: (update: (value: boolean) => boolean) => void) {
  return (event: MouseEvent<HTMLElement>) => {
    event.preventDefault();
    setOpen((value) => !value);
  };
}

export function RunProcess({ turn, directory }: { turn: LocalTurn; directory: ToolDirectory }) {
  // **默认收起。** 处理过程是排查用的, 不是读的; 摊开一屏中间步骤会把真正要看的东西
  // (最终回答) 推到屏幕外面。
  //
  // 收起不等于没有进度: 摘要行上的 activityOf 一直在说当下在干什么, 所以运行中不打开
  // 也不会让用户对着一个静止的折叠条干等 —— 那正是它原先必须自动展开的理由。
  //
  // 手动开合一旦发生就一直算数, 包括手动展开后不再被任何自动逻辑收回去。
  const [manual, setManual] = useState<boolean | null>(null);
  const open = manual ?? false;
  const [, tick] = useState(0);

  useEffect(() => {
    if (turn.status !== "running") return;
    const timer = window.setInterval(() => tick((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [turn.status]);

  const metrics = metricsFor(turn.events, Date.now(), turn.startedAt);
  // 收起时**不建**时间线, 也不渲染它: 一轮长任务有上百个节点, 留在 DOM 里的话, 最终
  // 回答每流入一帧都要多走一遍这棵树。
  const timeline = open ? buildTimeline(turn.events, directory) : [];

  return (
    <details className={`run-process ${turn.status}`} open={open}>
      <summary onClick={toggle((update) => setManual((value) => update(value ?? open)))}>
        <span className={turn.status === "running" ? "pulse" : "run-status-dot"} />
        <strong>{statusText(turn.status)}</strong>
        <span className="run-activity">{activityOf(turn, directory)}</span>
        <RunMetrics metrics={metrics} />
        <ChevronIcon className="disclosure" />
      </summary>
      {open && <ol className="run-steps">
        {timeline.map((item) => {
          if (item.kind === "model") {
            return <ModelStep events={item.events} output={turn.outputs[item.key] ?? ""} turnStatus={turn.status} reason={item.reason} key={item.id} />;
          }
          if (item.kind === "tools") return <ToolCategoryStep category={item.category} groups={item.groups} turnStatus={turn.status} reason={item.reason} directory={directory} key={item.id} />;
          return <NoteStep event={item.event} key={item.id} />;
        })}
        {!timeline.length && <li className="run-step pending"><span className="step-dot" /><div className="step-main"><p className="step-line">正在建立本轮事件流…</p></div></li>}
      </ol>}
    </details>
  );
}

function RunMetrics({ metrics }: { metrics: ReturnType<typeof metricsFor> }) {
  return <span className="run-metrics">
    <span>{formatDuration(metrics.elapsedMs)}</span>
    <span>{metrics.modelCalls} 次模型</span>
    <span>{metrics.toolCalls} 次工具</span>
    <span title={tokenBreakdown(metrics)}>
      {metrics.estimated ? "约 " : ""}{formatTokens(metrics.totalTokens)} tokens
    </span>
  </span>;
}

/** 悬停才看的明细给整数: 这一栏正是用来核对上面那个 k 是怎么来的。 */
function tokenBreakdown(metrics: ReturnType<typeof metricsFor>) {
  const parts = [`输入 ${metrics.inputTokens}`, `输出 ${metrics.outputTokens}`, `思考 ${metrics.reasoningTokens}`, `缓存 ${metrics.cachedTokens}`];
  if (metrics.compactTokens > 0) parts.push(`其中上下文压缩 ${metrics.compactTokens}`);
  return parts.join(" · ");
}

function ModelStep({ events, output, turnStatus, reason }: { events: RunEvent[]; output: string; turnStatus: LocalTurn["status"]; reason: string }) {
  const started = events.find((event) => event.kind === "model_started");
  const completed = events.find((event) => event.kind === "model_completed");
  const failed = events.find((event) => event.kind === "model_failed");
  const usage = events.find((event) => event.kind === "model_usage");
  const reasoning = [...events].reverse().find((event) => event.kind === "model_reasoning_status");
  const thinking = stringValue(reasoning?.payload.status) === "started";
  const state = settle(failed ? "failed" : completed ? "done" : "running", turnStatus);
  const label = stringValue(started?.payload.model) || "模型调用";

  // 标题跟着状态走: 一个恒定的"模型"字样在跑的时候什么也没说, 而这一格正是用户盯着看
  // 的地方。思考与生成分开 —— 前者可能持续很久且不吐字, 看起来像卡住了。
  const title = state !== "running" ? "模型" : thinking ? "模型思考中…" : "模型生成中…";

  return <Step state={state} title={title} subject={label} meta={completed ? formatDuration(numberValue(completed.payload.elapsed_ms)) : ""} reason={reason}>
    {showsOutput(completed) && output && <div className="step-answer"><Markdown content={output} compact /></div>}
    {usage && <p className="step-line muted">
      <span className="step-chip">输入 {formatTokens(numberValue(usage.payload.input_tokens))}</span>
      <span className="step-chip">输出 {formatTokens(numberValue(usage.payload.output_tokens))}</span>
      {numberValue(usage.payload.reasoning_tokens) > 0 && <span className="step-chip">思考 {formatTokens(numberValue(usage.payload.reasoning_tokens))}</span>}
      {numberValue(usage.payload.cached_tokens) > 0 && <span className="step-chip">缓存 {formatTokens(numberValue(usage.payload.cached_tokens))}</span>}
      {Boolean(usage.payload.estimated) && <span className="step-chip">估算</span>}
    </p>}
    {failed && <p className="step-line danger">{stringValue(failed.payload.message) || stringValue(failed.payload.error_kind)}</p>}
  </Step>;
}

function ToolCategoryStep({ category, groups, turnStatus, reason, directory }: { category: ToolCategory; groups: ToolGroup[]; turnStatus: LocalTurn["status"]; reason: string; directory: ToolDirectory }) {
  const states = groups.map((group) => stateOfTool(group.events, turnStatus));
  const state: StepState = states.includes("running")
    ? "running"
    : states.includes("failed") ? "failed" : states.includes("cancelled") ? "cancelled" : "done";
  const elapsed = groups.reduce((total, group) => total + elapsedOfTool(group.events), 0);
  const names = [...new Set(groups.map((group) => toolNameOf(group.events)))];
  // 类别回答"哪一类事", 动作名回答"具体哪件事": 五个任务工具全归在"任务"这一类下,
  // 而"更新任务状态"与"创建任务列表"对看的人不是一回事。
  const actions = [...new Set(names.map((name) => toolActionLabel(name, directory)))];
  return <Step
    state={state}
    title={category.label}
    subject={actions.join(" · ")}
    badge={groups.length > 1 ? `${groups.length} 次` : undefined}
    meta={elapsed > 0 ? formatDuration(elapsed) : ""}
    reason={reason}
  >
    {groups.map((group) => <ToolCall events={group.events} turnStatus={turnStatus} directory={directory} key={group.id} />)}
  </Step>;
}

function stateOfTool(events: RunEvent[], turnStatus: LocalTurn["status"]): StepState {
  const completed = [...events].reverse().find((event) => event.kind === "tool_completed" || event.kind === "tool_cancelled");
  return settle(
    !completed
      ? "running"
      : completed.kind === "tool_cancelled" ? "cancelled" : completed.payload.error_summary ? "failed" : "done",
    turnStatus,
  );
}

function elapsedOfTool(events: RunEvent[]) {
  const completed = [...events].reverse().find((event) => event.kind === "tool_completed" || event.kind === "tool_cancelled");
  return completed ? numberValue(completed.payload.elapsed_ms) : 0;
}

function argumentsOf(event: RunEvent | undefined): Array<[string, string]> {
  const raw = event?.payload.arguments;
  if (!Array.isArray(raw)) return [];
  return raw.map((pair, index) => {
    const values = Array.isArray(pair) ? pair : [index, pair];
    return [stringValue(values[0]), stringValue(values[1])];
  });
}

function ToolCall({ events, turnStatus, directory }: { events: RunEvent[]; turnStatus: LocalTurn["status"]; directory: ToolDirectory }) {
  const latest = events.at(-1);
  const queued = events.find((event) => event.kind === "tool_queued");
  const prepared = events.find((event) => event.kind === "tool_prepared");
  const policy = events.find((event) => event.kind === "policy_resolved");
  const completed = [...events].reverse().find((event) => event.kind === "tool_completed" || event.kind === "tool_cancelled");
  const awaiting = events.some((event) => event.kind === "approval_requested") && !events.some((event) => event.kind === "approval_resolved");
  const name = stringValue(prepared?.payload.tool_name ?? latest?.payload.tool_name) || "工具";
  // prepare 归一化过的入参更贴近真正执行的东西; 但连 prepare 都没走到的调用只有排队时
  // 那一份, 而那正是最需要看参数的场合 —— 工具名写错时, 参数是唯一的线索。
  const args = prepared ? argumentsOf(prepared) : argumentsOf(queued);
  const targets = Array.isArray(prepared?.payload.targets) ? prepared.payload.targets.map(stringValue) : [];
  const targetCount = numberValue(prepared?.payload.target_count);
  const pending = !completed;
  const state = stateOfTool(events, turnStatus);

  // 逐层点开: 处理过程 -> 步骤 -> 调用详情。这一层也默认收起, 展开过就一直算数。
  const [open, setOpen] = useState(false);

  const summary = completed
    ? stringValue(completed.payload.error_summary) || stringValue(completed.payload.result_summary)
    : "";

  return <div className={`tool-call ${state}`}>
    <div className="tool-call-head">
      <span className="tool-call-name" title={name}>{toolActionLabel(name, directory)}</span>
      <span className="step-badge">{toolState(latest, completed, awaiting, state)}</span>
      {/* 没执行过的终态与"执行了然后失败了"是两回事, 摘要行必须说得出区别。 */}
      {completed && !wasExecuted(completed) && <span className="step-badge">未执行</span>}
      {completed && <span className="step-meta">{formatDuration(numberValue(completed.payload.elapsed_ms))}</span>}
    </div>
    {summary && <p className={`step-line ${state === "failed" ? "danger" : ""}`}>{summary}</p>}
    {awaiting && <p className="step-line warn">等待人类审批</p>}
    {(args.length > 0 || policy || targets.length > 0) && (
      <details className="step-detail" open={open}>
        <summary onClick={toggle(setOpen)}><ChevronIcon className="step-detail-caret" />调用详情</summary>
        {args.length > 0 && <dl className="step-args">{args.map(([key, value], index) => (
          <div key={`${key}-${index}`}><dt>{key}</dt><dd title={value}>{clip(value)}</dd></div>
        ))}</dl>}
        {targets.length > 0 && <dl className="step-args"><div>
          <dt>目标</dt>
          <dd title={targets.join("\n")}>
            {targets.slice(0, 6).join(", ")}
            {targetCount > targets.length ? ` 等 ${targetCount} 个` : ""}
          </dd>
        </div></dl>}
        {policy && <PolicyLine event={policy} />}
        {completed && <CompletionLine event={completed} />}
      </details>
    )}
  </div>;
}

function PolicyLine({ event }: { event: RunEvent }) {
  const decision = stringValue(event.payload.decision);
  const facts = Array.isArray(event.payload.risk_facts) ? event.payload.risk_facts.map(stringValue) : [];
  const rule = stringValue(event.payload.matched_rule_id);
  const detail = stringValue(event.payload.detail);
  return <p className={`step-line ${decision === "allow" ? "muted" : "warn"}`}>
    安全裁决 {decision}
    {event.payload.reason ? ` · ${stringValue(event.payload.reason)}` : ""}
    {rule ? ` · 规则 ${rule}` : ""}
    {facts.length > 0 ? ` · 风险 ${facts.join(", ")}` : ""}
    {detail ? ` · ${detail}` : ""}
  </p>;
}

/** 结构化的机制事实。摘要行是给人一眼看的, 这一行是排查时要对的数。 */
function CompletionLine({ event }: { event: RunEvent }) {
  const chips: string[] = [];
  const code = stringValue(event.payload.error_code);
  if (code) chips.push(`错误码 ${code}`);
  if (event.payload.exit_code !== null && event.payload.exit_code !== undefined) chips.push(`退出码 ${numberValue(event.payload.exit_code)}`);
  if (numberValue(event.payload.bytes_out) > 0) chips.push(`${numberValue(event.payload.bytes_out)} 字节`);
  if (numberValue(event.payload.artifact_count) > 0) chips.push(`${numberValue(event.payload.artifact_count)} 个产物`);
  if (event.payload.truncated) chips.push("输出已截断");
  if (event.payload.side_effect_unknown) chips.push("已改动的内容未知");
  if (!chips.length) return null;
  return <p className="step-line muted">{chips.join(" · ")}</p>;
}

function NoteStep({ event }: { event: RunEvent }) {
  if (event.kind === "context_compacted") {
    // 省下的是**上下文**, 花掉的是 token —— 两个方向相反的数, 所以这一行只讲省下多少,
    // 花掉多少并进上面那条合计里 (ADR-0037)。
    const summary = stringValue(event.payload.level) === "summary";
    const replaced = numberValue(event.payload.messages_replaced);
    const rewritten = numberValue(event.payload.blocks_rewritten);
    return <Step state="done" title="上下文压缩" subject={summary ? "摘要" : "降级为引用"}>
      <p className="step-line muted">
        省下 {formatTokens(numberValue(event.payload.tokens_saved))} tokens
        {summary ? ` · 顶替 ${replaced} 条消息` : ` · 改写 ${rewritten} 段工具输出`}
      </p>
    </Step>;
  }
  if (event.kind === "todo_updated") {
    const current = stringValue(event.payload.current);
    const done = numberValue(event.payload.done);
    const total = numberValue(event.payload.total);
    // 同一个事件既可能是"刚建好一张单子", 也可能是"划掉了一项"。done 为零且有条目时是
    // 前者 —— 说成"更新任务状态"会让一次新建看起来像一次改动。
    const action = total > 0 && done === 0 ? "创建任务列表" : "更新任务状态";
    return <Step state="done" title="任务" subject={action} badge={total > 0 ? `${done}/${total}` : undefined}>
      {current && <p className="step-line">当前：{current}</p>}
    </Step>;
  }
  return <Step state="done" title="任务" subject={numberValue(event.payload.revision) > 1 ? "更新计划" : "创建计划"}>
    <p className="step-line muted">
      {stringValue(event.payload.title)} · 修订 {numberValue(event.payload.revision)} · {numberValue(event.payload.step_count)} 个步骤
    </p>
  </Step>;
}

function Step({ state, title, subject, meta, badge, reason, children }: {
  state: StepState;
  title: string;
  subject?: string;
  meta?: string;
  badge?: string;
  reason?: string;
  children?: ReactNode;
}) {
  return <li className={`run-step ${state}`}>
    <span className="step-dot" />
    <div className="step-main">
      <div className="step-head">
        <span className="step-title">{title}</span>
        {subject && <span className="step-subject" title={subject}>{subject}</span>}
        {badge && <span className="step-badge">{badge}</span>}
        {meta && <span className="step-meta">{meta}</span>}
      </div>
      {reason && <p className="step-line muted">{reason}</p>}
      {children}
    </div>
  </li>;
}

/** turn 已经结束时，没有终态事件的步骤不能继续显示成"进行中"。 */
function settle(state: StepState, turnStatus: LocalTurn["status"]): StepState {
  if (state !== "running" || turnStatus === "running") return state;
  if (turnStatus === "cancelled") return "cancelled";
  return turnStatus === "failed" ? "failed" : "done";
}

function statusText(status: LocalTurn["status"]) {
  if (status === "running") return "Forge 正在处理";
  if (status === "completed") return "处理过程";
  if (status === "cancelled") return "处理已取消";
  return "处理失败";
}

function toolState(latest: RunEvent | undefined, completed: RunEvent | undefined, awaiting: boolean, state: StepState) {
  if (completed) return stringValue(completed.payload.status) || "完成";
  if (state !== "running") return "未完成";
  if (awaiting) return "等待审批";
  const labels: Record<string, string> = { tool_queued: "排队", tool_prepared: "已准备", policy_resolved: "安全检查", approval_resolved: "审批完成", tool_started: "执行中" };
  return latest ? labels[latest.kind] ?? "处理中" : "处理中";
}

function clip(value: string, limit = 480) {
  const flat = value.replaceAll("\n", "\\n");
  return flat.length > limit ? `${flat.slice(0, limit)}…` : flat;
}

function formatDuration(milliseconds: number) {
  return milliseconds >= 1000 ? `${(milliseconds / 1000).toFixed(1)}s` : `${Math.round(milliseconds)}ms`;
}

