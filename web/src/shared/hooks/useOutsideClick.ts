/** 点到元素外面就收起。下拉菜单用。
 *
 * 听 mousedown 而不是 click: click 要等 mouseup, 而这中间用户可能已经在别处按下并
 * 拖动了 —— 那时菜单还盖在上面。
 */

import { useEffect } from "react";
import type { RefObject } from "react";

export function useOutsideClick(active: boolean, ref: RefObject<HTMLElement | null>, onOutside: () => void) {
  useEffect(() => {
    if (!active) return;
    const close = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) onOutside();
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [active, ref, onOutside]);
}
