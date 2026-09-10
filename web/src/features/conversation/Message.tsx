/** 时间线上的一条消息: 落盘的历史正文, 以及用户自己说的那句。 */

import type { ReactNode } from "react";
import { Avatar, Flex, Typography, theme } from "antd";
import { CopyButton } from "@/shared/ui/CopyButton";
import { Markdown } from "@/shared/ui/Markdown";
import type { TranscriptEvent } from "@/types/session";

/** 正文栏的最大宽度。输入区用同一个数, 两处边缘才对得齐。 */
export const CONTENT_WIDTH = 860;

/** 一条消息的骨架: 头像 + 名字 + 正文 + 底部操作区。历史与本地轮次共用。 */
export function MessageFrame({
  role,
  footer,
  children,
}: {
  role: "user" | "assistant";
  /** 底部那一行。有用量的轮次把复制挂在用量行里, 就不必再给这个。 */
  footer?: ReactNode;
  children: ReactNode;
}) {
  const { token } = theme.useToken();
  const assistant = role === "assistant";
  return (
    <Flex gap={12} align="flex-start" style={{ width: "100%" }}>
      <Avatar
        size={30}
        shape="square"
        style={{
          flex: "0 0 auto",
          fontSize: 12,
          fontWeight: 700,
          background: assistant
            ? "color-mix(in srgb, var(--forge-accent) 18%, transparent)"
            : token.colorFillSecondary,
          color: assistant ? token.colorPrimary : token.colorTextSecondary,
        }}
      >
        {assistant ? "F" : "你"}
      </Avatar>
      <Flex vertical gap={4} style={{ flex: 1, minWidth: 0 }}>
        <Typography.Text strong>{assistant ? "Forge" : "你"}</Typography.Text>
        {children}
        {footer}
      </Flex>
    </Flex>
  );
}

/** 只有一个复制按钮的底行: 用户消息与没有处理过程的历史回答用它。 */
export function CopyFooter({ text }: { text: string }) {
  return (
    <Flex justify="flex-end" style={{ marginTop: 2 }}>
      <CopyButton content={text} />
    </Flex>
  );
}

export function Message({ item }: { item: TranscriptEvent }) {
  const text = item.payload.text ?? "";
  if (item.payload.role === "user") return <UserMessage text={text} />;
  return (
    <MessageFrame role="assistant" footer={<CopyFooter text={text} />}>
      <Markdown content={text} />
    </MessageFrame>
  );
}

/** 历史消息和刚发送的本地消息共用同一个操作区，避免复制能力只在刷新后才出现。 */
export function UserMessage({ text }: { text: string }) {
  return (
    <MessageFrame role="user" footer={<CopyFooter text={text} />}>
      <Typography.Paragraph style={{ margin: "3px 0 0", whiteSpace: "pre-wrap", lineHeight: 1.7 }}>
        {text}
      </Typography.Paragraph>
    </MessageFrame>
  );
}
