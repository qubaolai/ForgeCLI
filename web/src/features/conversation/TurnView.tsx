/** 一轮的完整呈现: 处理过程 + 底部用量。
 *
 * 本地轮次与历史轮次是同一个形状, 只是数据一个来自事件流, 一个来自落盘的快照。
 * 复制按钮一律贴着最后那行数字放 —— 本地轮次是用量胶囊那行, 历史轮次是过程用量那行。
 */

import { Alert, Flex } from "antd";
import { FinalMetrics } from "@/features/conversation/FinalMetrics";
import { MessageFrame, UserMessage } from "@/features/conversation/Message";
import { RunProcess } from "@/features/runProcess/RunProcess";
import { metricsFor } from "@/shared/lib/run/metrics";
import type { ToolDirectory } from "@/shared/lib/run/tools";
import type { LocalTurn } from "@/shared/lib/run/turn";
import { CopyButton } from "@/shared/ui/CopyButton";
import type { TranscriptEvent } from "@/types/session";

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
    <MessageFrame role="assistant">
      <RunProcess
        turn={run}
        directory={directory}
        actions={text ? <CopyButton content={text} compact /> : undefined}
      />
    </MessageFrame>
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
  const done = turn.status !== "running";
  return (
    <Flex vertical gap={26}>
      {turn.userText && <UserMessage text={turn.userText} />}
      <MessageFrame
        role="assistant"
        footer={
          <Flex align="flex-end" justify="space-between" gap={12} style={{ minHeight: 26, marginTop: 5 }}>
            {done ? <FinalMetrics metrics={metrics} /> : <span />}
            {turn.assistantText && <CopyButton content={turn.assistantText} />}
          </Flex>
        }
      >
        <RunProcess turn={turn} directory={directory} />
        {turn.error && <Alert type="error" showIcon title={turn.error} />}
      </MessageFrame>
    </Flex>
  );
}
