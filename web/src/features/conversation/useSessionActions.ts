/** 新建 / 切换 / 删除会话。
 *
 * 这三件事横跨三样东西: 会话正文的状态, 事件流里还没渲染的那一批, 以及工作区的
 * 重新拉取。所以它们不在 useConversation 里 —— 那一份不认识事件流。
 *
 * 顺序是有讲究的: 换会话时必须同时改状态和事件回调看到的作用域 (enterSession),
 * 并且把待渲染缓冲排空 —— 否则清空之后, 属于上一个会话的事件会自己长回来
 * (ADR-0048 决策 2)。
 */

import { useState } from "react";
import type { Conversation } from "@/features/conversation/useConversation";
import { api } from "@/shared/api/client";
import type { Session } from "@/types/session";

export function useSessionActions({
  conversation,
  discardPending,
  reloadWorkspace,
  onError,
}: {
  conversation: Conversation;
  /** 丢掉事件流里还没渲染的那一批。 */
  discardPending: () => void;
  reloadWorkspace: () => Promise<void>;
  onError: (message: string) => void;
}) {
  // 正在等待确认的那个会话 id。删除不可撤销, 所以走一个必须显式按下的对话框。
  const [confirmDelete, setConfirmDelete] = useState("");
  const [deleting, setDeleting] = useState(false);

  async function create() {
    try {
      const session = await api<Session>("/sessions", { method: "POST" });
      conversation.enterSession(session.session_id);
      conversation.clearTimeline();
      discardPending();
      await conversation.loadTranscript(session.session_id);
      await reloadWorkspace();
    } catch (reason) {
      onError((reason as Error).message);
    }
  }

  async function resume(sessionId: string) {
    try {
      await api(`/sessions/${sessionId}/resume`, { method: "POST" });
      conversation.enterSession(sessionId);
      conversation.setLocalTurns([]);
      discardPending();
      await conversation.loadTranscript(sessionId);
    } catch (reason) {
      onError((reason as Error).message);
    }
  }

  /** 删掉一个会话及其计划、处理过程与恢复点。不可撤销, 所以入口是两步确认。 */
  async function remove(sessionId: string) {
    try {
      setDeleting(true);
      // 后端只保证"已经从列表消失"就返回, 文件在它那边后台清 —— 所以这里拿不到
      // 也不该等一个"清了几个恢复点"的数字。
      const result = await api<{ current_session_id: string; cleanup: string }>(`/sessions/${sessionId}`, {
        method: "DELETE",
      });
      setConfirmDelete("");
      // 删的是当前会话时后端已经开了一个新的, 切过去 —— 不自己猜停在哪。
      if (result.current_session_id !== conversation.currentSessionId) {
        conversation.enterSession(result.current_session_id);
        conversation.clearTimeline();
        discardPending();
        await conversation.loadTranscript(result.current_session_id);
      }
      await reloadWorkspace();
    } catch (reason) {
      onError((reason as Error).message);
    } finally {
      setDeleting(false);
    }
  }

  return { confirmDelete, setConfirmDelete, deleting, create, resume, remove };
}
