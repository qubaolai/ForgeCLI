/** SSE 续传位置: `实例:游标` (ADR-0048 决策 2)。
 *
 * 单独一个模块, 因为写它的有两方: 事件流每收到一条就往前推, 快照查询带回自己的水位。
 * 两边各存一份的话, 它们对"从哪儿接着走"的理解会分叉 —— 而分叉的表现是页面少几条
 * 事件, 没有任何报错。
 */

import { useCallback, useMemo, useRef } from "react";

export type ResumePosition = {
  /** 当前位置; 空串表示"没有可用位置", 由服务端决定从哪儿开始。 */
  read: () => string;
  /** 事件流推进: 总是往前, 不做比较。 */
  advance: (token: string) => void;
  /** 采纳一个快照水位, 只往前不往后。 */
  adopt: (token?: string) => void;
  /** 位置作废: 换了项目, 或服务端要求重新同步。 */
  reset: () => void;
};

export function useResumePosition(): ResumePosition {
  const current = useRef("");

  const adopt = useCallback((token?: string) => {
    if (!token) return;
    // 快照带回来的水位可能比已经收到的事件旧 —— 取快照与接上事件流之间本来就有间隔。
    // 无条件覆盖会让那几条事件被再应用一遍, 而 `appendRunEvent` 不是幂等的。
    const [stream, cursor] = token.split(":");
    const [currentStream, currentCursor] = current.current.split(":");
    if (currentStream === stream && Number(currentCursor) >= Number(cursor)) return;
    current.current = token;
  }, []);

  const read = useCallback(() => current.current, []);
  const advance = useCallback((token: string) => {
    current.current = token;
  }, []);
  const reset = useCallback(() => {
    current.current = "";
  }, []);

  // 必须是稳定的同一个对象: 事件流把它当 effect 依赖, 每次 render 换一个新的会让
  // SSE 连接跟着重建 —— 而重建一次连接就是一次断线, 断线要走一整轮补发。
  return useMemo(() => ({ read, advance, adopt, reset }), [read, advance, adopt, reset]);
}
