/** 输入框那一块: 文本域, 姿态与模型两个菜单, 发送 / 停止。
 *
 * 输入法 (IME) 要单独挡一次: 中文输入按 Enter 是"选词", 不是"发送"。浏览器给的
 * `isComposing` 在部分输入法上不可靠, 所以另外用 composition 事件自己记一份。
 */

import { useRef, useState } from "react";
import { Button, Flex, Input, Typography } from "antd";
import { BorderOutlined, EnterOutlined, FolderOutlined } from "@ant-design/icons";
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
  projectName,
  showProjectHint,
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
  projectName: string;
  showProjectHint: boolean;
}) {
  const composingRef = useRef(false);
  const [focused, setFocused] = useState(false);
  const offline = connection === "stopped";
  const sendable = Boolean(value.trim()) && !offline;

  return (
    <div className={`composer ${focused ? "focused" : ""} ${offline ? "offline" : ""}`}>
      {showProjectHint && projectName && (
        <Flex align="center" gap={8} className="composer-project" aria-label={`当前项目 ${projectName}`}>
          <FolderOutlined aria-hidden="true" />
          <Typography.Text type="secondary">当前项目</Typography.Text>
          <Typography.Text strong ellipsis>
            {projectName}
          </Typography.Text>
        </Flex>
      )}
      <Input.TextArea
        className="forge-composer-input"
        variant="borderless"
        value={value}
        autoSize={{ minRows: 3, maxRows: 10 }}
        disabled={offline}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
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
        <Flex align="center" gap={8} className="composer-menus">
          <ModeMenu value={stance} disabled={busy} onChange={onStance} />
          <ModelMenu
            current={currentModel}
            models={models}
            thinking={thinking}
            disabled={busy}
            onChoose={onChooseModel}
            onThinking={onThinking}
          />
        </Flex>
        <Flex className="composer-action">
          {busy ? (
            <Button
              type="primary"
              danger
              icon={<BorderOutlined />}
              loading={stopping}
              onClick={onCancel}
              aria-label={stopping ? "正在停止" : "停止生成"}
              title={stopping ? "正在停止" : "停止生成"}
            />
          ) : (
            <Button
              type="primary"
              icon={<EnterOutlined />}
              disabled={!sendable}
              onClick={onSend}
              aria-label="发送消息"
              title="发送消息"
            />
          )}
        </Flex>
      </Flex>
    </div>
  );
}
