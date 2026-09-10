/** 时间线上不属于模型也不属于工具的那几行: 窗口淘汰, 任务, 计划。 */

import { Flex, Tag, Typography } from "antd";
import type { RunEvent } from "@/shared/lib/run/events";
import { numberValue, stringValue } from "@/shared/lib/run/events";
import { formatTokens } from "@/shared/format";

export function NoteStep({ event }: { event: RunEvent }) {
  if (event.kind === "context_compacted") {
    // 省下的是**上下文**, 花掉的是 token —— 两个方向相反的数, 所以这一行只讲省下多少,
    // 花掉多少并进上面那条合计里 (ADR-0037)。
    //
    // 只剩一种压缩了: ADR-0041 删掉了"降级为引用"那一级, 工具结果正文现在本来就不进
    // 窗口, 没有可降的东西。
    return (
      <NoteLine
        tag="窗口淘汰"
        subject="交接说明"
        detail={`省下 ${formatTokens(numberValue(event.payload.tokens_saved))} tokens · 丢掉 ${numberValue(event.payload.messages_replaced)} 条消息`}
      />
    );
  }
  if (event.kind === "todo_updated") {
    const done = numberValue(event.payload.done);
    const total = numberValue(event.payload.total);
    // 同一个事件既可能是"刚建好一张单子", 也可能是"划掉了一项"。done 为零且有条目时是
    // 前者 —— 说成"更新任务状态"会让一次新建看起来像一次改动。
    const current = stringValue(event.payload.current);
    return (
      <NoteLine
        tag="任务"
        subject={total > 0 && done === 0 ? "创建任务列表" : "更新任务状态"}
        badge={total > 0 ? `${done}/${total}` : undefined}
        detail={current ? `当前：${current}` : ""}
      />
    );
  }
  return (
    <NoteLine
      tag="任务"
      subject={numberValue(event.payload.revision) > 1 ? "更新计划" : "创建计划"}
      detail={`${stringValue(event.payload.title)} · 修订 ${numberValue(event.payload.revision)} · ${numberValue(event.payload.step_count)} 个步骤`}
    />
  );
}

function NoteLine({
  tag,
  subject,
  badge,
  detail,
}: {
  tag: string;
  subject: string;
  badge?: string;
  detail: string;
}) {
  return (
    <Flex gap="small" align="center" wrap>
      <Tag>{tag}</Tag>
      <Typography.Text>{subject}</Typography.Text>
      {badge && <Tag color="blue">{badge}</Tag>}
      {detail && <Typography.Text type="secondary">{detail}</Typography.Text>}
    </Flex>
  );
}
