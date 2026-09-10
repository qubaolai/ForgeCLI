/** 一轮的处理过程。
 *
 * **叙述常驻, 工具折叠。**
 *
 * 原先是整轮塞进一个默认收起的 `<details>`, 而最终回答另起一段。那个形状撑不住一条
 * 硬约束: 最终回答必须流式, 而循环在发请求之前分不出哪一次调用是最后一次 —— 于是
 * 只能每次都流, 流出来的叙述必然已经显示过。既然已经显示过, 就不能再撤回它 (撤回
 * 的表现是"一段话闪一下又消失"), 只能让它留下。
 *
 * 留下之后, "最终回答"不再是特例: 它只是最后一段叙述。两边不再各画一遍同一段文字。
 */

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Alert, Flex, Typography } from "antd";
import { NarrationBlock } from "@/features/runProcess/NarrationBlock";
import { NoteStep } from "@/features/runProcess/NoteStep";
import { RunMetrics } from "@/features/runProcess/RunMetrics";
import { statusText } from "@/features/runProcess/stepState";
import { ToolRun } from "@/features/runProcess/ToolRun";
import { activityOf } from "@/shared/lib/run/activity";
import { metricsFor } from "@/shared/lib/run/metrics";
import type { ToolDirectory } from "@/shared/lib/run/tools";
import { buildTimeline } from "@/shared/lib/run/timeline";
import type { LocalTurn } from "@/shared/lib/run/turn";
import { terminalFailureDetail } from "@/shared/lib/run/turn";

/** 状态点的四种样子; 具体颜色在 styles/index.css 的 .run-dot 里。 */
const DOT_STATE: Record<string, string> = {
  running: "running",
  completed: "done",
  cancelled: "cancelled",
  failed: "failed",
};

export function RunProcess({
  turn,
  directory,
  actions,
}: {
  turn: LocalTurn;
  directory: ToolDirectory;
  /** 挂在用量行右端的操作。历史轮次的复制按钮走这里 —— 它旁边就是 tokens。 */
  actions?: ReactNode;
}) {
  // 跑的时候每秒重画一次: 耗时与"正在做什么"都随时间走, 没有事件也要跟着动。
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
  const running = turn.status === "running";

  return (
    <Flex vertical gap="small">
      {timeline.map((item) => {
        if (item.kind === "model") {
          return (
            <NarrationBlock
              events={item.events}
              output={turn.outputs[item.key] ?? ""}
              terminal={!running}
              key={item.id}
            />
          );
        }
        if (item.kind === "tools")
          return (
            <ToolRun
              groups={item.groups}
              turnStatus={turn.status}
              reason={item.reason}
              directory={directory}
              key={item.id}
            />
          );
        return <NoteStep event={item.event} key={item.id} />;
      })}
      {!timeline.length && !failureDetail && (
        <Typography.Text type="secondary">正在建立本轮事件流…</Typography.Text>
      )}
      {failureDetail && !modelFailureShown && <Alert type="error" showIcon title={failureDetail} />}
      {/* 指标行放在最后: 一轮跑完之后才有意义, 跑的过程中它一直在变, 摆在顶上会让眼睛
          跟着数字跑而不是跟着内容走。 */}
      <Flex align="center" gap={10} className="run-meta">
        <span className={`run-dot ${DOT_STATE[turn.status] ?? "done"}`} />
        <Typography.Text type="secondary" ellipsis style={{ fontSize: 11, flex: "0 1 auto" }}>
          {running ? activityOf(turn, directory) : statusText(turn.status)}
        </Typography.Text>
        <span style={{ marginInlineStart: "auto", display: "flex", alignItems: "center", gap: 6 }}>
          <RunMetrics metrics={metrics} />
          {actions}
        </span>
      </Flex>
    </Flex>
  );
}
