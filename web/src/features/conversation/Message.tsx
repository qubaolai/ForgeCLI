/** 时间线上的一条消息: 落盘的历史正文, 以及用户自己说的那句。 */

import { CopyButton } from "@/shared/ui/CopyButton";
import { Markdown } from "@/shared/ui/Markdown";
import type { TranscriptEvent } from "@/types/session";

export function Message({ item }: { item: TranscriptEvent }) {
  const user = item.payload.role === "user";
  const text = item.payload.text ?? "";
  if (user) return <UserMessage text={text} />;
  return (
    <article className="message assistant">
      <div className="avatar">F</div>
      <div>
        <strong>Forge</strong>
        <Markdown content={text} />
        <div className="message-footer">
          <span />
          <CopyButton content={text} className="message-copy-outside" />
        </div>
      </div>
    </article>
  );
}

/** 历史消息和刚发送的本地消息共用同一个操作区，避免复制能力只在刷新后才出现。 */
export function UserMessage({ text }: { text: string }) {
  return (
    <article className="message user">
      <div className="avatar">你</div>
      <div>
        <strong>你</strong>
        <p>{text}</p>
        <div className="message-footer">
          <span />
          <CopyButton content={text} className="message-copy-outside" />
        </div>
      </div>
    </article>
  );
}
