/** 事件到了之后干什么。
 *
 * 连接本身、退避重连与合帧都由 useRunEvents 自己管 (ADR-0048 决策 5); 这里只回答
 * "这条事件该落到界面的哪一块"。分出来是因为这一整块回调既不属于会话正文, 也不属于
 * 任何一个功能 —— 它是把它们接到一起的那根线。
 */

import type { Conversation } from "@/features/conversation/useConversation";
import type { ResumePosition } from "@/features/runEvents/resumePosition";
import { useRunEvents } from "@/features/runEvents/useRunEvents";
import { isTerminalEvent } from "@/shared/lib/run/events";
import { appendRunEvent } from "@/shared/lib/run/turn";

export function useRunEventWiring({
  projectId,
  resumePosition,
  conversation,
  reloadPrompts,
  reloadProjects,
  refreshWorkspace,
  onError,
}: {
  projectId: string | null;
  resumePosition: ResumePosition;
  conversation: Conversation;
  reloadPrompts: () => Promise<void>;
  reloadProjects: () => Promise<void>;
  refreshWorkspace: () => void;
  onError: (message: string) => void;
}) {
  // 事件流自己管连接, 退避与合帧; 这里只说"到了之后干什么" (ADR-0048 决策 5)。
  return useRunEvents(projectId, resumePosition, {
    // 事件缓冲跨会话共用: 重连补发时会带上切换之前那个会话的尾巴。两个会话都有
    // turn_0001, 不按归属丢掉就会叠进当前时间线 (ADR-0048 决策 2)。
    accepts: (event) => {
      const scope = conversation.sessionScopeRef.current;
      return !scope || !event.session_id || event.session_id === scope;
    },
    onBatch: (batch) => {
      conversation.setLocalTurns((items) =>
        batch.reduce((carry, event) => appendRunEvent(carry, event), items),
      );
    },
    onEvent: (event, flush) => {
      if (event.kind === "prompt_requested" || event.kind === "prompt_resolved") {
        void reloadPrompts().catch((reason: Error) => onError(reason.message));
      }
      // 后端会在首个工具启动前连续发布整批 tool_queued，最后一条 queue_position=0。
      // 立刻提交完整批次，避免普通流式事件的 33ms 合并窗口把它吞到完成事件后面。
      if (event.kind === "tool_queued" && Number(event.payload.queue_position ?? 0) === 0) {
        flush();
      }
      if (isTerminalEvent(event)) {
        flush();
        conversation.setBusy(false);
        refreshWorkspace();
        window.setTimeout(() => conversation.syncFinishedTurn(event.turn_id), 60);
      }
      if (["approval_requested", "approval_resolved", "plan_proposed", "todo_updated"].includes(event.kind)) {
        refreshWorkspace();
      }
    },
    onResync: () => {
      // 位置已经被 hook 丢掉了; 这里负责重新取一次快照, 由快照带回新水位。
      const scope = conversation.sessionScopeRef.current;
      if (scope) void conversation.loadTranscript(scope).catch(() => undefined);
      refreshWorkspace();
    },
    onOpen: (reconnected) => {
      void reloadPrompts().catch((reason: Error) => onError(reason.message));
      if (reconnected) {
        // 断线期间可能换了进程：项目、会话和运行态都要重新对齐。
        reloadProjects().catch(() => undefined);
        refreshWorkspace();
      }
    },
    onError,
  });
}
