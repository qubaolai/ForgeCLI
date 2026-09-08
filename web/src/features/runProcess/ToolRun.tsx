/** 一组同一动作的工具活动: 收起时一行, 展开是一张平表。 */

import { useState } from "react";
import type { MouseEvent } from "react";
import { ChevronIcon } from "@/shared/ui/icons";
import { formatElapsed } from "@/shared/format";
import type { RunEvent } from "@/shared/lib/run/events";
import { numberValue, stringValue } from "@/shared/lib/run/events";
import type { LocalTurn } from "@/shared/lib/run/turn";
import type { ToolDirectory, ToolGroup } from "@/shared/lib/run/tools";
import { summariseTools, toolActionLabel, wasExecuted } from "@/shared/lib/run/tools";
import type { StepState } from "@/features/runProcess/stepState";
import { stateOfTool, toolState } from "@/features/runProcess/stepState";

/** `<details>` 的 toggle 是异步事件；受控用法必须自己同步翻转，否则会被重渲染覆盖。 */
function toggle(setOpen: (update: (value: boolean) => boolean) => void) {
  return (event: MouseEvent<HTMLElement>) => {
    event.preventDefault();
    setOpen((value) => !value);
  };
}

/** 一组同一动作的工具活动: 收起时一行, 展开是一张平表。 */
export function ToolRun({
  groups,
  turnStatus,
  reason,
  directory,
}: {
  groups: ToolGroup[];
  turnStatus: LocalTurn["status"];
  reason: string;
  directory: ToolDirectory;
}) {
  const states = groups.map((group) => stateOfTool(group.events, turnStatus));
  const state: StepState = states.includes("running")
    ? "running"
    : states.includes("pending")
      ? "pending"
      : states.includes("failed")
        ? "failed"
        : states.includes("cancelled")
          ? "cancelled"
          : "done";
  const failures = states.filter((item) => item === "failed").length;
  const summary = summariseTools(groups, directory);
  // 工具组一律默认收起；用户主动展开之后，状态变化不强行改 open。
  const [open, setOpen] = useState(false);
  const active = state === "pending" || state === "running";

  return (
    <details className={`tool-line ${state}`} open={open}>
      <summary onClick={toggle(setOpen)}>
        <ChevronIcon className="tool-caret" />
        {/* 命令要一个动词才读得通:「正在mvn -q compile」不是话。 */}
        {active && summary.mono && (
          <span className="tool-running-verb">{state === "running" ? "正在跑" : "准备运行"}</span>
        )}
        <span className={`tool-what ${summary.mono ? "mono" : ""}`}>
          {active && !summary.mono ? (state === "running" ? "正在" : "准备调用") : ""}
          {summary.text}
        </span>
        {/* 次数单独一列, 不拼进文字: 拼进去要做"读取文件"→"读取 6 个文件"的动宾拆分,
          而那对"执行 Shell 命令"这类标题拆不开。 */}
        {summary.count > 1 && <span className="tool-count">{summary.count} 次</span>}
        {/* 失败计数上到摘要行: 一屏十几行里唯一发生了事的就是它, 收起时也得看得见。 */}
        {failures > 0 && <span className="tool-flag">{failures} 个失败</span>}
        {state === "cancelled" && <span className="tool-flag">已取消</span>}
      </summary>
      <div className="tool-line-body">
        {/* 类别与裁决理由只在展开后给: 收起时那一行要回答"它做了什么", 不是"它属于哪一类"。 */}
        {/* 派发本身不再发决策摘要, 所以这里剩下的都是真的有话说的 (连续无新信息, 格式
          损坏重试)。 */}
        {reason && <p className="step-reason">{reason}</p>}
        {groups.map((group) => (
          <ToolCall events={group.events} turnStatus={turnStatus} directory={directory} key={group.id} />
        ))}
      </div>
    </details>
  );
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
function ToolCall({
  events,
  turnStatus,
  directory,
}: {
  events: RunEvent[];
  turnStatus: LocalTurn["status"];
  directory: ToolDirectory;
}) {
  const latest = events.at(-1);
  const queued = events.find((event) => event.kind === "tool_queued");
  const prepared = events.find((event) => event.kind === "tool_prepared");
  const policy = events.find((event) => event.kind === "policy_resolved");
  const completed = [...events]
    .reverse()
    .find((event) => event.kind === "tool_completed" || event.kind === "tool_cancelled");
  const awaiting =
    events.some((event) => event.kind === "approval_requested") &&
    !events.some((event) => event.kind === "approval_resolved");
  const name = stringValue(prepared?.payload.tool_name ?? latest?.payload.tool_name) || "工具";
  // prepare 归一化过的入参更贴近真正执行的东西; 但连 prepare 都没走到的调用只有排队时
  // 那一份, 而那正是最需要看参数的场合 —— 工具名写错时, 参数是唯一的线索。
  const args = prepared ? argumentsOf(prepared) : argumentsOf(queued);
  const targets = Array.isArray(prepared?.payload.targets) ? prepared.payload.targets.map(stringValue) : [];
  const targetCount = numberValue(prepared?.payload.target_count);
  const state = stateOfTool(events, turnStatus);
  const stateText = toolState(latest, completed, awaiting, state);
  const summary = completed
    ? stringValue(completed.payload.error_summary) || stringValue(completed.payload.result_summary)
    : "";
  // 目标优先于入参: 它是裁决层解析出来的结果, 比模型写进去的那一份准。
  const subject = targets.length
    ? targets.slice(0, 3).join(", ") + (targetCount > targets.length ? ` 等 ${targetCount} 个` : "")
    : args.map(([key, value]) => `${key}=${clip(value)}`).join(" ");

  return (
    <div className={`call-row ${state}`}>
      <span className="call-tool">{toolActionLabel(name, directory)}</span>
      <span className="call-subject" title={subject}>
        {subject}
      </span>
      {stateText && <span className="call-state">{stateText}</span>}
      {/* 没执行过的终态与"执行了然后失败了"是两回事。 */}
      {completed && !wasExecuted(completed) && stringValue(completed.payload.status) !== "not_run" && (
        <span className="call-state">未执行</span>
      )}
      <span className="call-took">
        {completed ? formatElapsed(numberValue(completed.payload.elapsed_ms)) : ""}
      </span>
      {summary && <p className={`call-note ${state === "failed" ? "danger" : ""}`}>{summary}</p>}
      {awaiting && <p className="call-note warn">等待人类审批</p>}
      {policy && (
        <div className="call-note">
          <PolicyLine event={policy} />
        </div>
      )}
    </div>
  );
}

function argumentsOf(event: RunEvent | undefined): Array<[string, string]> {
  const raw = event?.payload.arguments;
  if (!Array.isArray(raw)) return [];
  return raw.map((pair, index) => {
    const values = Array.isArray(pair) ? pair : [index, pair];
    return [stringValue(values[0]), stringValue(values[1])];
  });
}

function PolicyLine({ event }: { event: RunEvent }) {
  const decision = stringValue(event.payload.decision);
  const facts = Array.isArray(event.payload.risk_facts) ? event.payload.risk_facts.map(stringValue) : [];
  const rule = stringValue(event.payload.matched_rule_id);
  const detail = stringValue(event.payload.detail);
  return (
    <p className={`step-line ${decision === "allow" ? "muted" : "warn"}`}>
      安全裁决 {decision}
      {event.payload.reason ? ` · ${stringValue(event.payload.reason)}` : ""}
      {rule ? ` · 规则 ${rule}` : ""}
      {facts.length > 0 ? ` · 风险 ${facts.join(", ")}` : ""}
      {detail ? ` · ${detail}` : ""}
    </p>
  );
}

function clip(value: string, limit = 480) {
  const flat = value.replaceAll("\n", "\\n");
  return flat.length > limit ? `${flat.slice(0, limit)}…` : flat;
}
