/** 时间线上不属于模型也不属于工具的那几行: 窗口淘汰, 任务, 计划。 */

import type { ReactNode } from "react";
import type { RunEvent } from "@/shared/lib/run/events";
import { numberValue, stringValue } from "@/shared/lib/run/events";
import { formatTokens } from "@/shared/format";
import type { StepState } from "@/features/runProcess/stepState";

export function NoteStep({ event }: { event: RunEvent }) {
  if (event.kind === "context_compacted") {
    // 省下的是**上下文**, 花掉的是 token —— 两个方向相反的数, 所以这一行只讲省下多少,
    // 花掉多少并进上面那条合计里 (ADR-0037)。
    //
    // 只剩一种压缩了: ADR-0041 删掉了"降级为引用"那一级, 工具结果正文现在本来就不进
    // 窗口, 没有可降的东西。
    const replaced = numberValue(event.payload.messages_replaced);
    return (
      <Step state="done" title="窗口淘汰" subject="交接说明">
        <p className="step-line muted">
          省下 {formatTokens(numberValue(event.payload.tokens_saved))} tokens · 丢掉 {replaced} 条消息
        </p>
      </Step>
    );
  }
  if (event.kind === "todo_updated") {
    const current = stringValue(event.payload.current);
    const done = numberValue(event.payload.done);
    const total = numberValue(event.payload.total);
    // 同一个事件既可能是"刚建好一张单子", 也可能是"划掉了一项"。done 为零且有条目时是
    // 前者 —— 说成"更新任务状态"会让一次新建看起来像一次改动。
    const action = total > 0 && done === 0 ? "创建任务列表" : "更新任务状态";
    return (
      <Step state="done" title="任务" subject={action} badge={total > 0 ? `${done}/${total}` : undefined}>
        {current && <p className="step-line">当前：{current}</p>}
      </Step>
    );
  }
  return (
    <Step
      state="done"
      title="任务"
      subject={numberValue(event.payload.revision) > 1 ? "更新计划" : "创建计划"}
    >
      <p className="step-line muted">
        {stringValue(event.payload.title)} · 修订 {numberValue(event.payload.revision)} ·{" "}
        {numberValue(event.payload.step_count)} 个步骤
      </p>
    </Step>
  );
}

function Step({
  state,
  title,
  subject,
  meta,
  badge,
  reason,
  children,
}: {
  state: StepState;
  title: string;
  subject?: string;
  meta?: string;
  badge?: string;
  reason?: string;
  children?: ReactNode;
}) {
  return (
    <li className={`run-step ${state}`}>
      <span className="step-dot" />
      <div className="step-main">
        <div className="step-head">
          <span className="step-title">{title}</span>
          {subject && (
            <span className="step-subject" title={subject}>
              {subject}
            </span>
          )}
          {badge && <span className="step-badge">{badge}</span>}
          {meta && <span className="step-meta">{meta}</span>}
        </div>
        {reason && <p className="step-line muted">{reason}</p>}
        {children}
      </div>
    </li>
  );
}
