/** 输入框那一块: 文本域, 姿态与模型两个菜单, 发送 / 停止。
 *
 * 输入法 (IME) 要单独挡一次: 中文输入按 Enter 是"选词", 不是"发送"。浏览器给的
 * `isComposing` 在部分输入法上不可靠, 所以另外用 composition 事件自己记一份。
 */

import { useRef } from "react";
import { Button, Flex, Input, Typography } from "antd";
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
  const composingRef = useRef(false);
  const offline = connection === "stopped";
  const sendable = Boolean(value.trim()) && !offline;

  return (
    <div className={`composer ${offline ? "offline" : ""}`}>
      <Input.TextArea
        variant="borderless"
        value={value}
        autoSize={{ minRows: 3, maxRows: 10 }}
        disabled={offline}
        style={{ padding: "14px 16px", lineHeight: 1.6 }}
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
            if (sendable && !busy) onSend();
          }
        }}
        placeholder={
          offline ? "连不上本地服务，请重新运行 forge 后刷新页面" : "描述你想理解、规划或修改的工程任务…"
        }
      />
      <Flex align="center" gap={8} wrap className="composer-bar">
        <ModeMenu value={stance} disabled={busy} onChange={onStance} />
        <ModelMenu
          current={currentModel}
          models={models}
          thinking={thinking}
          disabled={busy}
          onChoose={onChooseModel}
          onThinking={onThinking}
        />
        <Typography.Text type="secondary" style={{ marginInlineStart: "auto", fontSize: 10 }}>
          Enter 发送 · Shift+Enter 换行
        </Typography.Text>
        {busy ? (
          <Button danger onClick={onCancel} loading={stopping}>
            {stopping ? "停止中" : "停止"}
          </Button>
        ) : (
          <Button type="primary" disabled={!sendable} onClick={onSend}>
            发送 ↑
          </Button>
        )}
      </Flex>
    </div>
  );
}
