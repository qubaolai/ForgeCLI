/** 复制到剪贴板。复制结果自己显示 1.6 秒再退回 —— 剪贴板没有别的办法让人确认它成了。 */

import { useEffect, useRef, useState } from "react";

export function CopyButton({
  content,
  className = "",
  compact = false,
  label = "复制",
}: {
  content: string;
  className?: string;
  compact?: boolean;
  label?: string;
}) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const resetTimer = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (resetTimer.current !== null) window.clearTimeout(resetTimer.current);
    },
    [],
  );

  async function copy() {
    try {
      await navigator.clipboard.writeText(content);
      setState("copied");
    } catch {
      setState("failed");
    }
    if (resetTimer.current !== null) window.clearTimeout(resetTimer.current);
    resetTimer.current = window.setTimeout(() => setState("idle"), 1600);
  }

  const text = state === "copied" ? "已复制" : state === "failed" ? "复制失败" : label;
  return (
    <button
      type="button"
      className={`copy-button ${state} ${className}`}
      onClick={copy}
      aria-label={text}
      title={text}
    >
      <CopyIcon checked={state === "copied"} />
      {!compact && <span>{text}</span>}
    </button>
  );
}

function CopyIcon({ checked }: { checked: boolean }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      {checked ? (
        <path d="m5 12 4 4L19 6" />
      ) : (
        <>
          <rect x="8" y="8" width="11" height="11" rx="2" />
          <path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3" />
        </>
      )}
    </svg>
  );
}
