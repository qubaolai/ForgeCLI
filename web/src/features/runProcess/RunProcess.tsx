/** 一轮的处理过程：运行时逐步展示，完成后折叠过程，最终回答保持可见。 */

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Alert, Collapse, Flex, Typography } from "antd";
import { NarrationBlock } from "@/features/runProcess/NarrationBlock";
import { NoteStep } from "@/features/runProcess/NoteStep";
import { RunMetrics } from "@/features/runProcess/RunMetrics";
import { statusText } from "@/features/runProcess/stepState";
import { ToolRun } from "@/features/runProcess/ToolRun";
import { activityOf } from "@/shared/lib/run/activity";
import { metricsFor } from "@/shared/lib/run/metrics";
import type { ToolDirectory } from "@/shared/lib/run/tools";
import { buildTimeline } from "@/shared/lib/run/timeline";
import type { TimelineItem } from "@/shared/lib/run/timeline";
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

  const answer = timeline.find((item) => item.kind === "model" && item.key === turn.answerRequestId);
  const toolCount = timeline.reduce(
    (count, item) => count + (item.kind === "tools" ? item.groups.length : 0),
    0,
  );
  // 等整轮成功收尾再折叠；单次工具结束、子任务完成或模型仍在流式输出时不触发。
  // 没有可显示的最终回答时保留过程，避免折叠掉仅有的可见内容。
  const collapseProcess = turn.status === "completed" && Boolean(answer) && toolCount > 0;
  function renderItem(item: TimelineItem) {
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
          childEvents={turn.events.filter((event) => {
            if (!event.agent_id) return false;
            const start = turn.events.find(
              (candidate) => candidate.agent_id === event.agent_id && candidate.kind === "sub_agent_started",
            );
            return item.groups.some((group) => group.id === String(start?.payload.parent_tool_call_id));
          })}
          turnStatus={turn.status}
          reason={item.reason}
          directory={directory}
          key={item.id}
        />
      );
    return <NoteStep event={item.event} key={item.id} />;
  }

  return (
    <Flex vertical gap="small">
      {collapseProcess && answer ? (
        <>
          <Collapse
            key={turn.clientId}
            ghost
            size="small"
            defaultActiveKey={[]}
            styles={{ header: { padding: "4px 0" }, body: { padding: "8px 0" } }}
            items={[
              {
                key: "process",
                label: (
                  <Typography.Text type="secondary">查看处理过程 · {toolCount} 次工具调用</Typography.Text>
                ),
                children: (
                  <Flex vertical gap="small">
                    {timeline.filter((item) => item !== answer).map(renderItem)}
                  </Flex>
                ),
              },
            ]}
          />
          {renderItem(answer)}
        </>
      ) : (
        timeline.map(renderItem)
      )}
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
