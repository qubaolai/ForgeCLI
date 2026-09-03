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
  stringValue,
  ToolDirectory,
  ToolGroup,
  summariseTools,
  terminalFailureDetail,
  toolActionLabel,
  toolNameOf,
  wasExecuted,
} from "./runModel";

type StepState = "running" | "done" | "failed" | "cancelled";

// 耗时低于这个数就不显示。只读工具普遍是个位数毫秒, 逐行标出来只会让真正慢的那一次
// 淹没在里面。
const SLOW_ENOUGH_MS = 200;

/** `<details>` 的 toggle 是异步事件；受控用法必须自己同步翻转，否则会被重渲染覆盖。 */
function toggle(setOpen: (update: (value: boolean) => boolean) => void) {
  return (event: MouseEvent<HTMLElement>) => {
    event.preventDefault();
    setOpen((value) => !value);
  };
}

export function RunProcess({ turn, directory }: { turn: LocalTurn; directory: ToolDirectory }) {
  // **叙述常驻, 工具折叠。**
  //
  // 原先是整轮塞进一个默认收起的 `<details>`, 而最终回答另起一段。那个形状撑不住一条
  // 硬约束: 最终回答必须流式, 而循环在发请求之前分不出哪一次调用是最后一次 —— 于是
  // 只能每次都流, 流出来的叙述必然已经显示过。既然已经显示过, 就不能再撤回它 (撤回
  // 的表现是"一段话闪一下又消失"), 只能让它留下。
  //
  // 留下之后, "最终回答"不再是特例: 它只是最后一段叙述。两边不再各画一遍同一段文字。
  const [, tick] = useState(0);
  useEffect(() => {
    if (turn.status !== "running") return;
    const timer = window.setInterval(() => tick((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [turn.status]);

  const metrics = metricsFor(turn.events, Date.now(), turn.startedAt);
  const timeline = buildTimeline(turn.events, directory, turn.outputs);
  const failureDetail = terminalFailureDetail(turn.events);
  // model_failed 已在它自己的模型节点中显示；终态详情只补没有模型失败事件的异常路径，
  // 否则同一句供应商错误会出现两遍。
  const modelFailureShown = turn.events.some((event) => event.kind === "model_failed");

  return (
    <section className={`run-flow ${turn.status}`}>
      {timeline.map((item) => {
        if (item.kind === "model") {
          return <NarrationBlock events={item.events} output={turn.outputs[item.key] ?? ""} terminal={turn.status !== "running"} key={item.id} />;
        }
        if (item.kind === "tools") return <ToolRun groups={item.groups} turnStatus={turn.status} reason={item.reason} directory={directory} key={item.id} />;
        return <NoteStep event={item.event} key={item.id} />;
      })}
      {!timeline.length && !failureDetail && <p className="run-flow-pending">正在建立本轮事件流…</p>}
      {failureDetail && !modelFailureShown && <p className="run-flow-error" role="alert">{failureDetail}</p>}
      {/* 指标行放在最后: 一轮跑完之后才有意义, 跑的过程中它一直在变, 摆在顶上会让眼睛
          跟着数字跑而不是跟着内容走。 */}
      <footer className="run-flow-meta">
        <span className={turn.status === "running" ? "pulse" : "run-status-dot"} />
        <span className="run-activity">{turn.status === "running" ? activityOf(turn, directory) : statusText(turn.status)}</span>
        <RunMetrics metrics={metrics} />
      </footer>
    </section>
  );
}

/**
 * 一次模型调用说的话。**总是可见**, 不折叠。
 *
 * 它可能是过程叙述 ("我先看一下目录"), 也可能是最终回答 —— 在这里不区分, 因为区分
 * 需要等调用收尾, 而那时字已经流完了。
 */
function NarrationBlock({ events, output, terminal }: { events: RunEvent[]; output: string; terminal: boolean }) {
  const completed = events.find((event) => event.kind === "model_completed");
  const failed = events.find((event) => event.kind === "model_failed");
  const reasoning = [...events].reverse().find((event) => event.kind === "model_reasoning_status");
  const thinking = stringValue(reasoning?.payload.status) === "started";

  if (failed) {
    return <p className="run-flow-error">{stringValue(failed.payload.message) || stringValue(failed.payload.error_kind)}</p>;
  }
  if (!output.trim()) {
    // 供应商异常路径可能只有 turn_failed，没有 model_failed/model_completed。此时本轮已经
    // 结束，不能让先前的 reasoning_started 继续显示成“思考中”。错误由终态详情展示。
    if (terminal) return null;
    // 还没吐字。思考与生成分开说 —— 思考可能持续很久且一个字都不吐, 看起来像卡住了。
    return completed ? null : <p className="run-flow-waiting">{thinking ? "思考中…" : "生成中…"}</p>;
  }
  return <div className="run-flow-text"><Markdown content={output} /></div>;
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

/** 一组同一动作的工具活动: 收起时一行, 展开是一张平表。 */
function ToolRun({ groups, turnStatus, reason, directory }: { groups: ToolGroup[]; turnStatus: LocalTurn["status"]; reason: string; directory: ToolDirectory }) {
  const states = groups.map((group) => stateOfTool(group.events, turnStatus));
  const state: StepState = states.includes("running")
    ? "running"
    : states.includes("failed") ? "failed" : states.includes("cancelled") ? "cancelled" : "done";
  const elapsed = groups.reduce((total, group) => total + elapsedOfTool(group.events), 0);
  const failures = states.filter((item) => item === "failed").length;
  const summary = summariseTools(groups, directory);
  const [open, setOpen] = useState(false);

  // 运行中不给折叠三角: 还没有"详情"可展开, 摆一个点不动的三角只会让人去点。
  // 那一行自己说清楚在干嘛, 加一个呼吸点表示它还在跑。
  if (state === "running") {
    return <p className="tool-line running">
      <span className="pulse" />
      {/* 命令要一个动词才读得通:「正在mvn -q compile」不是话。 */}
      {summary.mono
        ? <><span className="tool-running-verb">正在跑</span><span className="tool-what mono">{summary.text}</span></>
        : <span className="tool-what">正在{summary.text}</span>}
      {summary.count > 1 && <span className="tool-count">{summary.count} 次</span>}
    </p>;
  }

  return <details className={`tool-line ${state}`} open={open}>
    <summary onClick={toggle(setOpen)}>
      <ChevronIcon className="tool-caret" />
      <span className={`tool-what ${summary.mono ? "mono" : ""}`}>{summary.text}</span>
      {/* 次数单独一列, 不拼进文字: 拼进去要做"读取文件"→"读取 6 个文件"的动宾拆分,
          而那对"执行 Shell 命令"这类标题拆不开。 */}
      {summary.count > 1 && <span className="tool-count">{summary.count} 次</span>}
      {/* 失败计数上到摘要行: 一屏十几行里唯一发生了事的就是它, 收起时也得看得见。 */}
      {failures > 0 && <span className="tool-flag">{failures} 个失败</span>}
      {state === "cancelled" && <span className="tool-flag">已取消</span>}
      {/* 耗时只在够久时才占一列: 一屏 17 行 1–4ms 是噪音, 而它们挤掉的正是 31 秒那一行
          该有的显眼程度。 */}
      {elapsed >= SLOW_ENOUGH_MS && <span className="tool-took">{formatDuration(elapsed)}</span>}
    </summary>
    <div className="tool-line-body">
      {/* 类别与裁决理由只在展开后给: 收起时那一行要回答"它做了什么", 不是"它属于哪一类"。 */}
      {/* 派发本身不再发决策摘要, 所以这里剩下的都是真的有话说的 (连续无新信息, 格式
          损坏重试)。 */}
      {reason && <p className="step-reason">{reason}</p>}
      {groups.map((group) => <ToolCall events={group.events} turnStatus={turnStatus} directory={directory} key={group.id} />)}
    </div>
  </details>;
}

function stateOfTool(events: RunEvent[], turnStatus: LocalTurn["status"]): StepState {
  const completed = [...events].reverse().find((event) => event.kind === "tool_completed" || event.kind === "tool_cancelled");
  return settle(
    completed ? (completed.kind === "tool_cancelled" ? "cancelled" : completed.payload.error_summary ? "failed" : "done") : "running",
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

/**
 * 展开层里的一行 = 一次调用。
 *
 * 原先这里是三层嵌套 (决策摘要 → 类别 → 工具名 → 状态 → 调用详情), 把摘要行已经说过的
 * 话又说了三遍, 而真正要看的参数与目标躺在第四层。一次读文件因此占掉半屏。
 *
 * 现在一次调用就是一行: 工具 · 目标 · 结果 · 耗时。参数和裁决只在真的有话说时才多给
 * 一行 —— 大多数只读调用没有。
 */
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
  const state = stateOfTool(events, turnStatus);
  const summary = completed
    ? stringValue(completed.payload.error_summary) || stringValue(completed.payload.result_summary)
    : "";
  // 目标优先于入参: 它是裁决层解析出来的结果, 比模型写进去的那一份准。
  const subject = targets.length
    ? targets.slice(0, 3).join(", ") + (targetCount > targets.length ? ` 等 ${targetCount} 个` : "")
    : args.map(([key, value]) => `${key}=${clip(value)}`).join(" ");

  return <div className={`call-row ${state}`}>
    <span className="call-tool">{toolActionLabel(name, directory)}</span>
    <span className="call-subject" title={subject}>{subject}</span>
    <span className="call-state">{toolState(latest, completed, awaiting, state)}</span>
    {/* 没执行过的终态与"执行了然后失败了"是两回事。 */}
    {completed && !wasExecuted(completed) && <span className="call-state">未执行</span>}
    <span className="call-took">{completed ? formatDuration(numberValue(completed.payload.elapsed_ms)) : ""}</span>
    {summary && <p className={`call-note ${state === "failed" ? "danger" : ""}`}>{summary}</p>}
    {awaiting && <p className="call-note warn">等待人类审批</p>}
    {policy && <div className="call-note"><PolicyLine event={policy} /></div>}
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
    //
    // 只剩一种压缩了: ADR-0041 删掉了"降级为引用"那一级, 工具结果正文现在本来就不进
    // 窗口, 没有可降的东西。
    const replaced = numberValue(event.payload.messages_replaced);
    return <Step state="done" title="窗口淘汰" subject="交接说明">
      <p className="step-line muted">
        省下 {formatTokens(numberValue(event.payload.tokens_saved))} tokens · 丢掉 {replaced} 条消息
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
