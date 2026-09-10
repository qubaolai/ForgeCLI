/** 一组同一动作的工具活动: 收起时一行浅色文字, 展开是缩进的调用清单。
 *
 * 不用卡片也不用表格: 工具是"顺带发生的事", 给它一个带边框的框, 一轮跑十几次之后
 * 模型说的话就被这些框推散了。收起时它退到背景里, 点开才给逐次调用的详情。
 */

import { Collapse, Flex, Tag, Typography } from "antd";
import { formatElapsed } from "@/shared/format";
import type { RunEvent } from "@/shared/lib/run/events";
import { numberValue, stringValue } from "@/shared/lib/run/events";
import type { LocalTurn } from "@/shared/lib/run/turn";
import type { ToolDirectory, ToolGroup } from "@/shared/lib/run/tools";
import { summariseTools, toolActionLabel, wasExecuted } from "@/shared/lib/run/tools";
import type { StepState } from "@/features/runProcess/stepState";
import { stateOfTool, toolState } from "@/features/runProcess/stepState";

type Tone = "secondary" | "warning" | "danger";

/**
 * 展开层里的一行 = 一次调用。
 *
 * 原先这里是三层嵌套 (决策摘要 → 类别 → 工具名 → 状态 → 调用详情), 把摘要行已经说过的
 * 话又说了三遍, 而真正要看的参数与目标躺在第四层。一次读文件因此占掉半屏。
 */
type CallRow = {
  key: string;
  tool: string;
  subject: string;
  state: StepState;
  stateText: string;
  elapsedMs: number;
  notes: Array<{ text: string; tone: Tone }>;
};

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
  const rows = groups.map((group) => describeCall(group.id, group.events, turnStatus, directory));
  const states = rows.map((row) => row.state);
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
  const active = state === "pending" || state === "running";
  const elapsed = rows.reduce((total, row) => total + row.elapsedMs, 0);

  return (
    <Collapse
      ghost
      size="small"
      styles={{
        header: { padding: "2px 0", alignItems: "center" },
        title: { minWidth: 0 },
        icon: { paddingInlineEnd: 8, fontSize: 11, color: "var(--forge-muted)", opacity: 0.7 },
        body: { padding: 0 },
      }}
      items={[
        {
          key: "tools",
          label: (
            <Flex align="center" gap={8} style={{ minWidth: 0 }}>
              {/* 命令要一个动词才读得通:「正在mvn -q compile」不是话。 */}
              <Typography.Text
                type={state === "failed" ? "danger" : "secondary"}
                ellipsis
                className={state === "running" ? "tool-running" : undefined}
              >
                {active ? (state === "running" ? "正在" : "准备调用") : ""}
                {summary.text}
              </Typography.Text>
              {/* 次数单独一格, 不拼进文字: 拼进去要做"读取文件"→"读取 6 个文件"的动宾拆分,
                  而那对"执行 Shell 命令"这类标题拆不开。 */}
              {summary.count > 1 && (
                <Typography.Text type="secondary" style={{ fontSize: 10, flex: "0 0 auto" }}>
                  {summary.count} 次
                </Typography.Text>
              )}
              {/* 失败计数上到摘要行: 一屏十几行里唯一发生了事的就是它, 收起时也得看得见。 */}
              {failures > 0 && (
                <Tag bordered={false} color="error" style={{ fontSize: 10, marginInlineEnd: 0 }}>
                  {failures} 个失败
                </Tag>
              )}
              {state === "cancelled" && (
                <Tag bordered={false} style={{ fontSize: 10, marginInlineEnd: 0 }}>
                  已取消
                </Tag>
              )}
              {elapsed > 0 && (
                <Typography.Text
                  type="secondary"
                  style={{ fontSize: 10, marginInlineStart: "auto", flex: "0 0 auto" }}
                >
                  {formatElapsed(elapsed)}
                </Typography.Text>
              )}
            </Flex>
          ),
          children: (
            <div className="tool-body">
              {/* 裁决理由只在展开后给: 收起时那一行要回答"它做了什么", 不是"它属于哪一类"。
                  派发本身不再发决策摘要, 所以这里剩下的都是真的有话说的。 */}
              {reason && (
                <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                  {reason}
                </Typography.Text>
              )}
              {rows.map((row) => (
                <div className="call-row" key={row.key}>
                  <Typography.Text
                    type={row.state === "failed" ? "danger" : undefined}
                    className={row.state === "running" ? "tool-running" : undefined}
                    style={{ whiteSpace: "nowrap", fontSize: 12 }}
                  >
                    {row.tool}
                  </Typography.Text>
                  <Typography.Text type="secondary" className="call-subject" title={row.subject}>
                    {row.subject}
                  </Typography.Text>
                  <Typography.Text
                    type={row.state === "failed" ? "danger" : "secondary"}
                    style={{ fontSize: 10 }}
                  >
                    {row.stateText}
                  </Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 10 }}>
                    {row.elapsedMs > 0 ? formatElapsed(row.elapsedMs) : ""}
                  </Typography.Text>
                  {row.notes.map((note) => (
                    <Typography.Text className="call-note" type={note.tone} key={note.text}>
                      {note.text}
                    </Typography.Text>
                  ))}
                </div>
              ))}
            </div>
          ),
        },
      ]}
    />
  );
}

function describeCall(
  key: string,
  events: RunEvent[],
  turnStatus: LocalTurn["status"],
  directory: ToolDirectory,
): CallRow {
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
  const summary = completed
    ? stringValue(completed.payload.error_summary) || stringValue(completed.payload.result_summary)
    : "";

  const notes: CallRow["notes"] = [];
  if (summary) notes.push({ text: summary, tone: state === "failed" ? "danger" : "secondary" });
  if (awaiting) notes.push({ text: "等待人类审批", tone: "warning" });
  // 没执行过的终态与"执行了然后失败了"是两回事。
  if (completed && !wasExecuted(completed) && stringValue(completed.payload.status) !== "not_run") {
    notes.push({ text: "未执行", tone: "warning" });
  }
  if (policy) notes.push(policyNote(policy));

  return {
    key,
    tool: toolActionLabel(name, directory),
    // 目标优先于入参: 它是裁决层解析出来的结果, 比模型写进去的那一份准。
    subject: targets.length
      ? targets.slice(0, 3).join(", ") + (targetCount > targets.length ? ` 等 ${targetCount} 个` : "")
      : args.map(([field, value]) => `${field}=${clip(value)}`).join(" "),
    state,
    stateText: toolState(latest, completed, awaiting, state),
    elapsedMs: completed ? numberValue(completed.payload.elapsed_ms) : 0,
    notes,
  };
}

function argumentsOf(event: RunEvent | undefined): Array<[string, string]> {
  const raw = event?.payload.arguments;
  if (!Array.isArray(raw)) return [];
  return raw.map((pair, index) => {
    const values = Array.isArray(pair) ? pair : [index, pair];
    return [stringValue(values[0]), stringValue(values[1])];
  });
}

function policyNote(event: RunEvent): { text: string; tone: Tone } {
  const decision = stringValue(event.payload.decision);
  const facts = Array.isArray(event.payload.risk_facts) ? event.payload.risk_facts.map(stringValue) : [];
  const rule = stringValue(event.payload.matched_rule_id);
  const detail = stringValue(event.payload.detail);
  const text = [
    `安全裁决 ${decision}`,
    event.payload.reason ? stringValue(event.payload.reason) : "",
    rule ? `规则 ${rule}` : "",
    facts.length > 0 ? `风险 ${facts.join(", ")}` : "",
    detail,
  ]
    .filter(Boolean)
    .join(" · ");
  return { text, tone: decision === "allow" ? "secondary" : "warning" };
}

function clip(value: string, limit = 480) {
  const flat = value.replaceAll("\n", "\\n");
  return flat.length > limit ? `${flat.slice(0, limit)}…` : flat;
}
