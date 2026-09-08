/** 遮罩层统一的 Esc 关闭: 避免每个面板各写一份 window 监听。 */

import { useEffect } from "react";

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
