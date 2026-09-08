/** 会话正文: 会话列表, 历史消息, 本地轮次, 以及发送与停止。
 *
 * 这一整块从 App 里搬出来 (ADR-0048 决策 5)。它和模型设置、计划栏没有任何交集,
 * 混在一起时, 改一个模型下拉框要在同一个函数里翻过整条时间线的逻辑, 反过来也一样。
 *
 * 新建 / 切换 / 删除会话不在这里: 那三件事要同时动这里的状态、事件流的待渲染缓冲和
 * 工作区刷新 —— 那正是"把功能接到一起", 是 App 的活。这里只提供它们要用的那几块。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/shared/api/client";
import type { ResumePosition } from "@/features/runEvents/resumePosition";
import type { RunSnapshot } from "@/shared/lib/run/events";
import type { LocalTurn } from "@/shared/lib/run/turn";
import { failUnboundTurn, finishLocalTurn, newLocalTurn, restoreTurn } from "@/shared/lib/run/turn";
import type { Stance } from "@/shared/lib/stance";
import type { Session, TranscriptEvent, TurnRunState } from "@/types/session";

const DEFAULT_STANCE: Stance = { sandbox: "workspace_write", approval: "always" };

export type Conversation = ReturnType<typeof useConversation>;

export function useConversation(
  onError: (message: string) => void,
  resumePosition: ResumePosition,
  /** 发送时重新贴回底部; 由时间线的滚动那一份持有。 */
  followNow: () => void,
) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<TranscriptEvent[]>([]);
  const [restoredRuns, setRestoredRuns] = useState<LocalTurn[]>([]);
  const [localTurns, setLocalTurns] = useState<LocalTurn[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [stance, setStance] = useState<Stance>(DEFAULT_STANCE);
  // 当前会话, 给事件回调用。回调建立时闭包住的那个值会过期, 而过期的作用域判断比
  // 不判断更糟: 它会把新会话的事件当成外来的丢掉 (ADR-0048 决策 2)。
  const sessionScopeRef = useRef("");
  const transcriptRequestRef = useRef(0);

  useEffect(() => {
    sessionScopeRef.current = currentSessionId ?? "";
  }, [currentSessionId]);

  useEffect(() => {
    if (!busy) setStopping(false);
  }, [busy]);

  const restoredByTurn = useMemo(() => {
    const live = new Set(localTurns.map((turn) => turn.turnId));
    return new Map(
      restoredRuns
        .filter((turn) => turn.turnId && !live.has(turn.turnId))
        .map((turn) => [turn.turnId as string, turn]),
    );
  }, [restoredRuns, localTurns]);

  const loadConversation = useCallback(async () => {
    const [sessionResult, runResult] = await Promise.all([
      api<{ current_session_id: string; current_session: Session; items: Session[] }>("/sessions"),
      api<TurnRunState | null>("/turns/current"),
    ]);
    setSessions(sessionResult.items);
    setCurrentSessionId(sessionResult.current_session_id);
    setStance(sessionResult.current_session.mode ?? DEFAULT_STANCE);
    // 服务端才是"是否还在跑"的真相源：刷新页面后按它恢复运行态。
    setBusy(runResult?.status === "running");
  }, []);

  const loadTranscript = useCallback(
    async (sessionId: string) => {
      // 按请求代次 + 作用域双重校验 (ADR-0048 决策 5)。会话 A 的慢响应晚于会话 B 的
      // 到达时, 无条件写入会把 B 的正文和处理过程换成 A 的 —— 而页面上看不出异常。
      const version = ++transcriptRequestRef.current;
      const stale = () => version !== transcriptRequestRef.current;
      try {
        const result = await api<{ items: TranscriptEvent[] }>(`/sessions/${sessionId}/transcript`);
        if (stale()) return;
        setTranscript(result.items);
      } catch {
        if (stale()) return;
        setTranscript([]);
      }
      // 处理过程现在落盘 (sessions/<id>/runs.jsonl)，所以历史轮次也展得开。
      // 后端把落盘的与进程内还没收尾的那一轮合并后一起发。
      try {
        const runs = await api<{ items: RunSnapshot[]; session_id?: string; resume?: string }>("/runs");
        // 响应自报归属: 代次之外再核一次会话, 取消旧请求代替不了这一步 —— /runs 取的
        // 是"当前会话", 而请求发出到返回之间当前会话可能已经换了。
        if (stale() || (runs.session_id && runs.session_id !== sessionId)) return;
        setRestoredRuns(runs.items.map((item) => restoreTurn(item)));
        // 快照自带水位: 先应用快照, 再接水位之后的事件, 中间那一段不会掉 (ADR-0048 决策 2)。
        resumePosition.adopt(runs.resume);
      } catch {
        if (!stale()) setRestoredRuns([]);
      }
    },
    [resumePosition],
  );

  /** 一轮收尾之后跟服务端对一次账: 事件流给的是过程, 这里拿的是最终文本与错误。 */
  const syncFinishedTurn = useCallback(async function sync(turnId: string, attempt = 0) {
    try {
      const run = await api<TurnRunState | null>("/turns/current");
      if (run?.status === "running" && attempt < 6) {
        window.setTimeout(() => sync(turnId, attempt + 1), 80 * (attempt + 1));
        return;
      }
      setLocalTurns((items) => finishLocalTurn(items, turnId, run?.response?.text, run?.error));
    } catch {
      if (attempt < 2) window.setTimeout(() => sync(turnId, attempt + 1), 150);
    }
  }, []);

  async function send() {
    if (!message.trim() || busy) return;
    const text = message.trim();
    const local = newLocalTurn(text);
    try {
      setBusy(true);
      // 发出去就把上一条错误横幅收掉: 它说的是上一次的事。
      onError("");
      followNow();
      setLocalTurns((items) => [...items, local]);
      setMessage("");
      await api("/turns", { method: "POST", body: JSON.stringify({ text }) });
    } catch (reason) {
      setBusy(false);
      const detail = (reason as Error).message;
      setLocalTurns((items) => failUnboundTurn(items, local.clientId, detail));
      onError(detail);
    }
  }

  async function cancelTurn() {
    try {
      setStopping(true);
      await api("/turns/current/cancel", { method: "POST" });
    } catch (reason) {
      setStopping(false);
      onError((reason as Error).message);
    }
  }

  /** 只送要改的那一个轴; 另一个由后端保持不变。 */
  async function changeStance(patch: Partial<Stance>) {
    try {
      await api("/mode", { method: "POST", body: JSON.stringify(patch) });
      setStance((current) => ({ ...current, ...patch }));
    } catch (reason) {
      onError((reason as Error).message);
    }
  }

  /** 换到另一个会话或项目之前, 把属于上一个的正文清掉。 */
  function clearTimeline() {
    setTranscript([]);
    setLocalTurns([]);
  }

  /** 切到某个会话: 状态与事件回调看到的作用域必须同时改, 差一步就会丢事件。 */
  function enterSession(sessionId: string) {
    setCurrentSessionId(sessionId);
    sessionScopeRef.current = sessionId;
  }

  return {
    sessions,
    currentSessionId,
    transcript,
    localTurns,
    restoredByTurn,
    message,
    setMessage,
    busy,
    setBusy,
    stopping,
    stance,
    sessionScopeRef,
    setLocalTurns,

    loadConversation,
    loadTranscript,
    syncFinishedTurn,
    send,
    cancelTurn,
    changeStance,
    clearTimeline,
    enterSession,
  };
}
