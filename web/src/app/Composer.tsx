/** 输入框那一块: 文本域, 姿态与模型两个菜单, 发送 / 停止。
 *
 * 输入法 (IME) 要单独挡一次: 中文输入按 Enter 是"选词", 不是"发送"。浏览器给的
 * `isComposing` 在部分输入法上不可靠, 所以另外用 composition 事件自己记一份。
 */

import { useEffect, useRef } from "react";
import { shouldSendOnEnter } from "@/features/conversation/interaction";
import { ModeMenu } from "@/features/chrome/ModeMenu";
import { ModelMenu } from "@/features/chrome/ModelMenu";
import type { ConnectionState } from "@/features/runEvents/useRunEvents";
import type { Stance } from "@/shared/lib/stance";
import type { ThinkingView } from "@/types/admin";

export function Composer({
  value,
  onChange,
  onSend,
  connection,
  busy,
  stopping,
  onCancel,
  stance,
  onStance,
  currentModel,
  models,
  thinking,
  onChooseModel,
  onThinking,
}: {
  value: string;
  onChange: (text: string) => void;
  onSend: () => void;
  /** 连不上本地服务时整个输入区停用 —— 发出去也没人收。 */
  connection: ConnectionState;
  busy: boolean;
  stopping: boolean;
  onCancel: () => void;
  stance: Stance;
  onStance: (patch: Partial<Stance>) => void;
  currentModel: string;
  models: string[];
  thinking: ThinkingView;
  onChooseModel: (providerId: string, modelId: string) => void;
  onThinking: (mode: string, effort: string) => void;
}) {
  const boxRef = useRef<HTMLTextAreaElement>(null);
  const composingRef = useRef(false);
  const offline = connection === "stopped";

  // 跟着内容长高, 到 240px 封顶后转成内部滚动。
  useEffect(() => {
    const node = boxRef.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${Math.min(node.scrollHeight, 240)}px`;
  }, [value]);

  return (
    <form
      className={`composer ${offline ? "offline" : ""}`}
      onSubmit={(event) => {
        event.preventDefault();
        onSend();
      }}
    >
      <textarea
        ref={boxRef}
        value={value}
        rows={1}
        disabled={offline}
        onChange={(event) => onChange(event.target.value)}
        onCompositionStart={() => {
          composingRef.current = true;
        }}
        onCompositionEnd={() => {
          composingRef.current = false;
        }}
        onKeyDown={(event) => {
          if (
            shouldSendOnEnter(event.key, event.shiftKey, event.nativeEvent.isComposing, composingRef.current)
          ) {
            event.preventDefault();
            event.currentTarget.form?.requestSubmit();
          }
        }}
        placeholder={
          offline ? "连不上本地服务，请重新运行 forge 后刷新页面" : "描述你想理解、规划或修改的工程任务…"
        }
      />
      <div className="composer-bar">
        <ModeMenu value={stance} disabled={busy} onChange={onStance} />
        <ModelMenu
          current={currentModel}
          models={models}
          thinking={thinking}
          disabled={busy}
          onChoose={onChooseModel}
          onThinking={onThinking}
        />
        <span className="composer-hint">Enter 发送 · Shift+Enter 换行</span>
        {busy ? (
          <button type="button" className="stop" onClick={onCancel} disabled={stopping}>
            {stopping ? "停止中…" : "停止"}
          </button>
        ) : (
          <button type="submit" disabled={!value.trim() || offline}>
            发送 ↑
          </button>
        )}
      </div>
    </form>
  );
}
