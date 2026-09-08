/** 运行事件流: 连接, 退避重连, 续传, 以及把洪峰合成一帧一次 (ADR-0048 决策 5)。
 *
 * 从 `App` 里整块搬出来的理由是它有自己的一套状态机 —— 连接档位, 重连间隔, 待提交
 * 缓冲, 续传位置 —— 而这些与"页面上正在显示什么"完全无关。混在一起时, 改一个按钮的
 * 布局要先读懂重连退避。
 *
 * 事件往哪儿去由调用方说 (`onEvent` / `onBatch`)。这个 hook 不认识 turn, 也不认识
 * 提示卡片。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { STALE_SESSION } from "@/shared/api/client";
import type { RunEvent } from "@/shared/lib/run/events";
import type { ResumePosition } from "./resumePosition";

export type ConnectionState = "connecting" | "live" | "retrying" | "stopped";

export const connectionCopy: Record<ConnectionState, { label: string; hint: string }> = {
  connecting: { label: "连接中", hint: "正在建立本地事件流" },
  live: { label: "本地已连接", hint: "事件流正常" },
  retrying: { label: "重连中", hint: "本地服务不可达，正在按退避节奏自动重连" },
  stopped: { label: "已停止重试", hint: "长时间连不上本地服务；重新运行 forge 后刷新页面即可" },
};

// 一帧合并一次事件；33ms 对流式正文足够顺滑，又不会让每条增量都触发一次整树重渲染。
const flushIntervalMs = 33;

// 重连间隔从 1 秒开始逐次翻倍；一旦下一次要等超过一小时，就不再自动重试。
const reconnectBaseMs = 1000;
const reconnectFactor = 2;
const reconnectCeilingMs = 60 * 60 * 1000;

export type RunEventHandlers = {
  /** 这条事件属于哪个会话由调用方判定; 返回 false 表示丢弃, 位置照样已经推进。 */
  accepts: (event: RunEvent) => boolean;
  /** 合帧之后整批交出去。 */
  onBatch: (events: RunEvent[]) => void;
  /** 单条到达时的即时反应 (拉待答提示, 收尾一轮…), 在合帧之前。 */
  onEvent: (event: RunEvent, flushNow: () => void) => void;
  /** 服务端要求重新同步: 手里的位置已经作废。 */
  onResync: () => void;
  /** 连接建立。`reconnected` 为真表示断线期间可能换过进程, 全量对齐一次。 */
  onOpen: (reconnected: boolean) => void;
  onError: (message: string) => void;
};

export type RunEventStream = {
  connection: ConnectionState;
  retryDelay: number;
  /** 丢掉还没渲染的那一批。切会话时用: 它们属于上一个会话。 */
  discardPending: () => void;
  flushNow: () => void;
};

export function useRunEvents(
  projectId: string | null,
  position: ResumePosition,
  handlers: RunEventHandlers,
): RunEventStream {
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [retryDelay, setRetryDelay] = useState(0);
  const pending = useRef<RunEvent[]>([]);
  const flushHandle = useRef(0);
  const scope = useRef<string | null>(null);
  // 回调每次 render 都是新的; 存进 ref 之后连接就不必因为它们变了而重建 —— 重建
  // 连接等于一次断线, 而断线要走一整轮补发。
  const latest = useRef(handlers);
  latest.current = handlers;

  const flushEvents = useCallback(() => {
    flushHandle.current = 0;
    const batch = pending.current;
    if (!batch.length) return;
    pending.current = [];
    latest.current.onBatch(batch);
  }, []);

  const flushNow = useCallback(() => {
    if (flushHandle.current) window.clearTimeout(flushHandle.current);
    flushEvents();
  }, [flushEvents]);

  const discardPending = useCallback(() => {
    pending.current = [];
  }, []);

  useEffect(
    () => () => {
      if (flushHandle.current) window.clearTimeout(flushHandle.current);
    },
    [],
  );

  useEffect(() => {
    if (!projectId) return;
    if (scope.current !== projectId) {
      // 换项目就是换了一个事件 hub 实例, 它的游标从头开始。带着上一个项目的位置去
      // 重连, 新实例要跑到那个数字才会开始发 —— 中间的事件一条都看不到, 而页面上
      // 不会有任何异常 (ADR-0048 决策 2)。
      scope.current = projectId;
      position.reset();
      pending.current = [];
    }
    let source: EventSource | null = null;
    let timer = 0;
    let delay = reconnectBaseMs;
    let disposed = false;
    let opened = false;

    // 事件类型放在 data.kind 里，页面只订阅一个名字，后端新增事件不会被静默丢弃。
    const consume = (raw: MessageEvent<string>) => {
      // 位置先推进, 再决定这条要不要用: 丢掉的事件也占了一个游标, 不推进的话每次
      // 重连都会把它们重新要回来。
      if (raw.lastEventId) position.advance(raw.lastEventId);
      const event = JSON.parse(raw.data) as RunEvent;
      if (!latest.current.accepts(event)) return;
      latest.current.onEvent(event, flushNow);
      pending.current.push(event);
      // 用定时器而不是 requestAnimationFrame: 标签页切走时 rAF 完全不触发, 事件会一直
      // 堆在缓冲里不渲染也不释放。定时器在后台只是被降频, 缓冲照样能排空。
      if (!flushHandle.current) {
        flushHandle.current = window.setTimeout(flushEvents, flushIntervalMs);
      }
    };

    const scheduleRetry = () => {
      if (disposed) return;
      if (delay > reconnectCeilingMs) {
        // 间隔已经超过一小时，继续自动重试没有意义；刷新页面即可重新开始。
        setRetryDelay(0);
        setConnection("stopped");
        return;
      }
      const wait = delay;
      delay *= reconnectFactor;
      setRetryDelay(wait);
      setConnection("retrying");
      timer = window.setTimeout(connect, wait);
    };

    function connect() {
      if (disposed) return;
      const resume = position.read();
      source = new EventSource(`/api/v1/events${resume ? `?after=${encodeURIComponent(resume)}` : ""}`);
      source.addEventListener("run_event", consume as EventListener);
      source.addEventListener("resync_required", () => {
        // 实例不匹配 / 游标超前 / 缓冲过期, 后端一个处置。手里的位置已经作废, 拿它
        // 接着等只会一直等下去 —— 丢掉它, 让调用方重新取一次快照。
        position.reset();
        pending.current = [];
        latest.current.onResync();
      });
      source.addEventListener("server_stopping", () => {
        // 服务在正常退出。端口与会话密钥都是固定的，等它起来就能自己连回去。
        source?.close();
        source = null;
        delay = reconnectBaseMs;
        scheduleRetry();
      });
      source.onopen = () => {
        delay = reconnectBaseMs;
        setRetryDelay(0);
        setConnection("live");
        latest.current.onOpen(opened);
        opened = true;
      };
      source.onerror = () => {
        // 接管 EventSource 自带的固定间隔重连，才能做退避并在超过上限后停下来。
        source?.close();
        source = null;
        if (disposed) return;
        // 会话密钥跨重启复用，正常不会走到这里；真失效时给出可操作提示。
        fetch("/api/v1/bootstrap")
          .then(
            (response) => (response.status === 401 ? "stale" : "retry"),
            () => "retry",
          )
          .then((verdict) => {
            if (disposed) return;
            if (verdict === "stale") latest.current.onError(STALE_SESSION);
            scheduleRetry();
          });
      };
    }

    setConnection("connecting");
    connect();
    return () => {
      disposed = true;
      window.clearTimeout(timer);
      source?.close();
    };
  }, [projectId, position, flushEvents, flushNow]);

  return { connection, retryDelay, discardPending, flushNow };
}
