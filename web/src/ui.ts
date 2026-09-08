/** 跨功能共用的零碎: 路径缩写, 重试文案, 遮罩层的 Esc 关闭。
 *
 * 都不带状态, 也不认识任何一个功能 —— 认识了就该搬进那个功能自己的模块。
 */

import { useEffect } from "react";

export function formatDelay(milliseconds: number) {
  const seconds = Math.round(milliseconds / 1000);
  if (seconds < 60) return `${seconds} 秒后重试`;
  return `${Math.round(seconds / 60)} 分钟后重试`;
}

export function shortPath(path: string) {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.at(-1) || path;
}

/** 遮罩层统一的 Esc 关闭；避免每个面板各写一份 window 监听。 */
export function useEscape(active: boolean, onEscape: () => void) {
  useEffect(() => {
    if (!active) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") onEscape();
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [active, onEscape]);
}

/** 一段耗时的短写法。秒以下不显示小数, 分钟以上不显示秒。 */
export function formatElapsed(milliseconds: number) {
  return milliseconds >= 1000 ? `${(milliseconds / 1000).toFixed(1)}s` : `${Math.round(milliseconds)}ms`;
}
