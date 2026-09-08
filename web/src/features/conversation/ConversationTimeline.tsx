/** 时间线本体: 历史正文与本地轮次。 */

import { useEffect } from "react";
import type { Ref } from "react";
import { LocalTurnView, Message, RestoredTurnView } from "@/features/conversation/Timeline";
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
  useEffect(onContentChange, [onContentChange, transcript, localTurns]);

  return (
    <div className="timeline" ref={scrollRef} onScroll={onScroll}>
      {!transcript.length && !localTurns.length && (
        <div className="welcome">
          <div className="forge-mark">F</div>
          <h2>准备好了</h2>
          <p>描述你想理解、规划或修改的工程任务。</p>
        </div>
      )}
      {transcript.map((item) => {
        const run =
          item.payload.role === "assistant" ? restoredByTurn.get(item.payload.turn_id ?? "") : undefined;
        // 有过程就渲染过程 —— 最终回答是它最后一块叙述, 再画一遍 Message 就重复了。
        // 没有过程的旧会话 (runs.jsonl 之前的) 仍然只渲染回答, 那是它们全部的内容。
        if (run) return <RestoredTurnView run={run} item={item} directory={directory} key={item.event_id} />;
        return <Message item={item} key={item.event_id} />;
      })}
      {localTurns.map((turn) => (
        <LocalTurnView turn={turn} directory={directory} key={turn.clientId} />
      ))}
    </div>
  );
}
