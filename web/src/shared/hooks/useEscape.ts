/** 遮罩层统一的 Esc 关闭: 避免每个面板各写一份 window 监听。 */

import { useEffect } from "react";

export function useEscape(
  active: boolean,
  onEscape: () => void,
  /**
   * 叠在别的遮罩层上面时置真: 走捕获阶段并掐断后续监听, 于是一次 Esc 只关掉最上面
   * 这一层。不置真的话, 抽屉后面那个设置面板会跟着一起关。
   */
  exclusive = false,
) {
  useEffect(() => {
    if (!active) return;
    const close = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (exclusive) event.stopImmediatePropagation();
      onEscape();
    };
    window.addEventListener("keydown", close, exclusive);
    return () => window.removeEventListener("keydown", close, exclusive);
  }, [active, onEscape, exclusive]);
}
