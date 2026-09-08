/** 时间线的跟随滚动。
 *
 * 只要用户没有主动往上翻, 新内容到达时就贴着底部走; 一旦翻上去, 就停下来并给出
 * 一个"回到底部"的入口 —— 边流边被拽回底部, 是没法读上面那段的。
 *
 * 这里不认识对话数据: "内容变了"由渲染时间线的那个组件在 effect 里告诉它
 * (stickToBottom), 否则这个 hook 与 useConversation 会互相依赖, 谁也没法先建。
 */

import { useCallback, useRef, useState } from "react";
import { shouldAutoFollow } from "@/features/conversation/interaction";

export function useTimelineScroll() {
  const timelineRef = useRef<HTMLDivElement>(null);
  const autoFollowRef = useRef(true);
  const [showJumpToBottom, setShowJumpToBottom] = useState(false);

  /** 内容变了: 还在跟随就滚到底。下一帧再滚 —— 这一帧新内容还没量出高度。 */
  const stickToBottom = useCallback(() => {
    if (!autoFollowRef.current) return;
    const frame = window.requestAnimationFrame(() => {
      const timeline = timelineRef.current;
      if (timeline) timeline.scrollTop = timeline.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, []);

  const handleScroll = useCallback(() => {
    const timeline = timelineRef.current;
    if (!timeline) return;
    const follow = shouldAutoFollow(timeline.scrollHeight, timeline.scrollTop, timeline.clientHeight);
    autoFollowRef.current = follow;
    // 滚动事件很密集：状态没变就不要触发重渲染。
    setShowJumpToBottom((current) => (current === !follow ? current : !follow));
  }, []);

  const jumpToBottom = useCallback(() => {
    autoFollowRef.current = true;
    setShowJumpToBottom(false);
    timelineRef.current?.scrollTo({ top: timelineRef.current.scrollHeight, behavior: "smooth" });
  }, []);

  /** 重新开始跟随, 但这一刻不滚 —— 发消息时用: 新内容到了自然会跟上去。 */
  const followNow = useCallback(() => {
    autoFollowRef.current = true;
    setShowJumpToBottom(false);
  }, []);

  return { timelineRef, showJumpToBottom, stickToBottom, handleScroll, jumpToBottom, followNow };
}
