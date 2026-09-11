/** 时间线本体: 历史正文与本地轮次。 */

import { useEffect } from "react";
import type { Ref } from "react";
import { Avatar, Flex, Result, theme } from "antd";
import { CONTENT_WIDTH, Message } from "@/features/conversation/Message";
import { LocalTurnView, RestoredTurnView } from "@/features/conversation/TurnView";
import type { LocalTurn } from "@/shared/lib/run/turn";
import type { ToolDirectory } from "@/shared/lib/run/tools";
import type { TranscriptEvent } from "@/types/session";

export function ConversationTimeline({
  scrollRef,
  onScroll,
  onContentChange,
  transcript,
  localTurns,
  restoredByTurn,
  directory,
}: {
  scrollRef: Ref<HTMLDivElement>;
  onScroll: () => void;
  /** 内容变了: 交给跟随滚动决定要不要贴回底部。 */
  onContentChange: () => void | (() => void);
  transcript: TranscriptEvent[];
  localTurns: LocalTurn[];
  /** 历史轮次的处理过程, 按 turn_id 查。 */
  restoredByTurn: Map<string, LocalTurn>;
  directory: ToolDirectory;
}) {
  const { token } = theme.useToken();
  useEffect(onContentChange, [onContentChange, transcript, localTurns]);

  return (
    <div
      className="conversation-timeline"
      ref={scrollRef}
      onScroll={onScroll}
      style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "22px clamp(18px, 3vw, 44px)" }}
    >
      <div className="conversation-content" style={{ maxWidth: CONTENT_WIDTH, margin: "0 auto" }}>
        {!transcript.length && !localTurns.length && (
          <Result
            className="conversation-empty"
            icon={
              <Avatar size={46} shape="square" style={{ background: token.colorPrimary, fontSize: 23 }}>
                F
              </Avatar>
            }
            title="准备好了"
            subTitle="描述你想理解、规划或修改的工程任务。"
          />
        )}
        {/* 一条消息与下一条之间留 20px: 保持轮次分明, 同时让更多内容留在首屏。 */}
        <Flex vertical gap={20}>
          {transcript.map((item) => {
            const run =
              item.payload.role === "assistant" ? restoredByTurn.get(item.payload.turn_id ?? "") : undefined;
            // 有过程就渲染过程 —— 最终回答是它最后一块叙述, 再画一遍 Message 就重复了。
            // 没有过程的旧会话 (runs.jsonl 之前的) 仍然只渲染回答, 那是它们全部的内容。
            if (run)
              return <RestoredTurnView run={run} item={item} directory={directory} key={item.event_id} />;
            return <Message item={item} key={item.event_id} />;
          })}
          {localTurns.map((turn) => (
            <LocalTurnView turn={turn} directory={directory} key={turn.clientId} />
          ))}
        </Flex>
      </div>
    </div>
  );
}
