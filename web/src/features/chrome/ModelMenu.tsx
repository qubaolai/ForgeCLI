/** 顶栏的模型选择器, 带 thinking 档位。 */

import { useCallback, useEffect, useRef, useState } from "react";
import { CheckIcon, ChevronIcon } from "@/shared/ui/icons";
import type { ThinkingView } from "@/types";
import { useEscape } from "@/shared/hooks/useEscape";

/** 输入框旁的模型与思考强度入口: 这两项调得最勤, 不该每次都进设置页。
 *
 * 强度尤其如此 —— 它是随任务变的: 读代码用低档, 设计方案用高档, 而进设置页要三次点击。
 * 模型自己的默认值仍然在模型配置里填, 这里改的是**本进程**的临时覆盖, 不落盘。
 */
export function ModelMenu({
  current,
  models,
  thinking,
  disabled,
  onChoose,
  onThinking,
}: {
  current: string;
  models: string[];
  thinking: ThinkingView;
  disabled: boolean;
  onChoose: (providerId: string, modelId: string) => void;
  onThinking: (mode: string, effort: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  useEscape(
    open,
    useCallback(() => setOpen(false), []),
  );
  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", closeOutside);
    return () => document.removeEventListener("mousedown", closeOutside);
  }, [open]);
  const efforts = thinking.supported_efforts ?? [];
  const thinkingOn = thinking.mode === "on";
  const label = current ? current.split(":").slice(1).join(":") || current : "选择模型";
  return (
    <div className="mode-menu model-menu" ref={rootRef}>
      <button
        type="button"
        className="mode-trigger"
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((state) => !state)}
        title={current || "未设置模型"}
      >
        <span className="model-label">{label}</span>
        {thinkingOn && (
          <em className="thinking-badge">思考{thinking.effort ? ` · ${thinking.effort}` : ""}</em>
        )}
        <ChevronIcon className="mode-caret" />
      </button>
      {open && (
        <div className="mode-options model-options">
          <p className="menu-heading">模型</p>
          <ul role="listbox" aria-label="当前模型">
            {models.map((ref) => (
              <li key={ref}>
                <button
                  type="button"
                  role="option"
                  aria-selected={ref === current}
                  className={ref === current ? "active" : ""}
                  onClick={() => {
                    setOpen(false);
                    const [provider, ...rest] = ref.split(":");
                    onChoose(provider, rest.join(":"));
                  }}
                >
                  <span>
                    <strong>{ref.split(":").slice(1).join(":") || ref}</strong>
                    <small>{ref.split(":")[0]}</small>
                  </span>
                  {ref === current && <CheckIcon className="mode-check" />}
                </button>
              </li>
            ))}
            {!models.length && (
              <li>
                <p className="empty-copy">还没有已配置的模型，去设置页添加。</p>
              </li>
            )}
          </ul>
          {thinking.configured && (
            <>
              <p className="menu-heading">思考</p>
              <div className="thinking-row">
                <button
                  type="button"
                  className={thinkingOn ? "" : "active"}
                  onClick={() => onThinking("off", "")}
                >
                  关闭
                </button>
                <button
                  type="button"
                  className={thinkingOn ? "active" : ""}
                  onClick={() => onThinking("on", "")}
                >
                  开启
                </button>
              </div>
              {thinkingOn && efforts.length > 0 && (
                <div className="thinking-row">
                  {efforts.map((item) => (
                    <button
                      type="button"
                      key={item}
                      className={thinking.effort === item ? "active" : ""}
                      onClick={() => onThinking("", item)}
                    >
                      {item}
                    </button>
                  ))}
                </div>
              )}
              {thinkingOn && !efforts.length && <p className="field-help">该模型未声明可用强度。</p>}
            </>
          )}
        </div>
      )}
    </div>
  );
}
