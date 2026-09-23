/** 把一轮的事件排成给人看的步骤。 */

import type { RunEvent } from "@/shared/lib/run/events";
import { modelKey, stringValue } from "@/shared/lib/run/events";
import { isCompactionUsage } from "@/shared/lib/run/metrics";
import type { ToolCategory, ToolDirectory, ToolGroup } from "@/shared/lib/run/tools";
import { categoryOf, groupToolEvents, toolNameOf } from "@/shared/lib/run/tools";

export type TimelineItem =
  | { id: string; kind: "model"; key: string; sequence: number; events: RunEvent[]; reason: string }
  | {
      id: string;
      kind: "tools";
      sequence: number;
      category: ToolCategory;
      mergeKey: string;
      groups: ToolGroup[];
      reason: string;
    }
  | { id: string; kind: "note"; sequence: number; event: RunEvent; reason: string };

const NOTE_KINDS = ["todo_updated", "plan_proposed", "context_compacted"];

export function groupModelEvents(events: RunEvent[]) {
  const groups = new Map<string, RunEvent[]>();
  for (const event of events) {
    if (!event.kind.startsWith("model_")) continue;
    // 压缩的用量事件带着自己的 request_id, 不挡掉的话会在时间线上多出一个只有用量、
    // 没有开始也没有结束的"模型调用", 而且永远显示成运行中。它属于压缩那一行。
    if (isCompactionUsage(event)) continue;
    const key = modelKey(event);
    groups.set(key, [...(groups.get(key) ?? []), event]);
  }
  return [...groups.entries()].map(([id, items]) => ({ id, events: items }));
}

/**
 * 这一次模型调用有没有话可显示。
 *
 * 判据必须用**渲染器用的那份数据** (outputs), 不能用 model_completed 里的 text_chars:
 * 实测模型在每次工具调用旁边会吐一个空白字符, 于是 text_chars=1 —— 计数说"它说话了",
 * 而屏幕上什么都没有。那个看不见的节点横在中间, 让相邻的工具活动永远合并不了。
 *
 * 规则是"渲染不出东西就不进时间线", 所以判据和渲染必须看同一份东西。
 */
function speaks(events: RunEvent[], text: string): boolean {
  if (events.some((event) => event.kind === "model_failed")) return true;
  const completed = events.find((event) => event.kind === "model_completed");
  // 还没收尾: 留着, 那是"思考中…/生成中…"的位置。
  return !completed || text.trim().length > 0;
}

export function buildTimeline(
  events: RunEvent[],
  directory: ToolDirectory = {},
  outputs: Record<string, string> = {},
): TimelineItem[] {
  events = events.filter((event) => !event.agent_id);
  const items: TimelineItem[] = groupModelEvents(events)
    // 一个字都没吐、只发了工具调用的模型节点不进时间线。
    //
    // 它渲染不出任何东西, 却横在两组工具活动中间, 让相邻合并永远不成立 —— 实测一屏
    // 18 次工具调用一次都没并起来, 就是因为每次调用前面都隔着一个看不见的模型节点。
    // "模型跑了几次"由底部的指标行回答, 不需要在流里占位。
    .filter((group) => speaks(group.events, outputs[group.id] ?? ""))
    .map((group) => ({
      id: `model-${group.id}`,
      kind: "model",
      key: group.id,
      events: group.events,
      sequence: Math.min(...group.events.map((event) => event.sequence)),
      reason: "",
    }));
  for (const group of groupToolEvents(events)) {
    const name = toolNameOf(group.events);
    items.push({
      id: `tools-${group.id}`,
      kind: "tools",
      sequence: Math.min(...group.events.map((event) => event.sequence)),
      category: categoryOf(name, directory),
      // 合并粒度用**工具名**而不是类别: 按类别并会把 6 次 fs_find 与 10 次 fs_read 压成
      // 一行"18 次工具调用" —— 从一个极端跳到另一个。这里原先用的是后端的 ToolAction,
      // 但那个字段已随 ADR-0042 删除, 而工具名比它更细也更准: `fs_read` 与 `git_read`
      // 曾经同属 "read", 并成一行其实读不出发生了什么。
      // 认不出名字的调用退回类别, 不让它们各自成行。
      mergeKey: name || categoryOf(name, directory).id,
      groups: [group],
      reason: "",
    });
  }
  for (const event of events) {
    if (!NOTE_KINDS.includes(event.kind)) continue;
    items.push({ id: `note-${event.event_id}`, kind: "note", event, sequence: event.sequence, reason: "" });
  }
  items.sort((a, b) => a.sequence - b.sequence);
  return merge(attachReasons(items, events));
}

/**
 * 决策摘要不单独占一行，挂到它解释的那一步上。
 *
 * 每次派发工具循环都会发一条 DECISION_SUMMARY，摊平之后时间线就是"工具 / 决策 /
 * 工具 / 决策"交替，一半的行在重复上一行已经写明的事。更要命的是它们夹在工具之间，
 * 连着六次读文件因此没有一次是"相邻"的，聚合根本合不起来。
 *
 * 归属看的是**下一步开始之前**：循环先发 TOOL_QUEUED 再发决策，所以一条决策说的是
 * 它上面那一步，不是下面那一步。
 */
function attachReasons(items: TimelineItem[], events: RunEvent[]): TimelineItem[] {
  const decisions = events.filter((event) => event.kind === "decision_summary");
  if (!decisions.length) return items;
  let cursor = 0;
  return items.map((item, index) => {
    const next = items[index + 1]?.sequence ?? Number.POSITIVE_INFINITY;
    let reason = "";
    while (cursor < decisions.length && decisions[cursor].sequence < next) {
      reason = reason || stringValue(decisions[cursor].payload.reason_summary);
      cursor += 1;
    }
    return reason ? { ...item, reason } : item;
  });
}

/** 相邻的同类调用并成一段: 连着读六个文件是一件事, 摊成六行只会淹掉真正要看的东西。 */
function merge(items: TimelineItem[]): TimelineItem[] {
  const merged: TimelineItem[] = [];
  for (const item of items) {
    const previous = merged.at(-1);
    if (item.kind === "tools" && previous?.kind === "tools" && previous.mergeKey === item.mergeKey) {
      previous.groups.push(...item.groups);
      continue;
    }
    merged.push(item.kind === "tools" ? { ...item, groups: [...item.groups] } : item);
  }
  return merged;
}
