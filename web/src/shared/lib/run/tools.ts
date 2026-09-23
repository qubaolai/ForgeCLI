/** 工具调用: 怎么分组, 属于哪一类, 收起时那一行怎么写。 */

import type { RunEvent } from "@/shared/lib/run/events";
import { stringValue } from "@/shared/lib/run/events";
import type { ToolDirectoryItem } from "@/types/tools";

/**
 * 后端工具目录里的一条, 取自 `GET /api/v1/tools` 的 `all`。
 *
 * `title` 与 `read_only` 都是后端 `Tool` 上的事实, 前端不另存一份。这里曾经有一张手抄
 * 的名字表, 它按定义会过期。
 */
export type ToolDirectoryEntry = { title: string; read_only: boolean };

export type ToolDirectory = Record<string, ToolDirectoryEntry>;

export type ToolGroup = { id: string; events: RunEvent[] };

export type ToolCategory = { id: string; label: string };

/**
 * 工具活动收成的那一行。
 *
 * `count` 交给渲染层单独摆一格, 不拼进 text ——
 * 拼进去就要在标题里做"读取文件"→"读取 6 个文件"这种动宾拆分, 而那对
 * "执行 Shell 命令""按符号名找定义"根本拆不开。
 */
export type ToolSummary = { text: string; count: number };

const OTHER_TOOLS: ToolCategory = { id: "other", label: "其他工具" };
const READ_TOOLS: ToolCategory = { id: "read", label: "读取文件" };

/**
 * 按"做了哪一类事"归并 (ADR-0054 决策 2 的九个同名工具 + 自有工具)。
 *
 * 目录里没有的名字按目录报的 `read_only` 归类: 只读的算"读取文件", 其余归"其他工具"。
 * 不按命名空间猜: 模型编出来的名字猜成"读取文件"就是在替一次没发生过的写入洗白。
 */
const TOOL_CATEGORIES: Array<{ id: string; label: string; tools: string[] }> = [
  { id: "agent", label: "子任务调研", tools: ["Agent"] },
  { id: "shell", label: "执行命令", tools: ["Bash", "shell_run"] },
  { id: "write", label: "修改文件", tools: ["Edit", "Write", "file_edit", "file_write", "fs_apply_patch"] },
  {
    id: "read",
    label: "读取文件",
    tools: ["Read", "Glob", "Grep", "LSP", "file_read", "fs_read", "search_text"],
  },
  {
    id: "planning",
    label: "任务",
    tools: ["plan_read", "plan_write", "TodoWrite", "todo_write", "todo_set_status"],
  },
  { id: "memory", label: "记忆", tools: ["memory_write", "memory_forget"] },
  { id: "question", label: "提问", tools: ["AskUserQuestion", "ask_user"] },
];

/**
 * 步骤标题上那一行: 认得的用它自己的 title, 认不得的老老实实显示工具名。
 *
 * 目录里没有的名字退回工具名本身: 编一个动作名出来会让一次没发生过的操作看起来发生过。
 * 目录还没拉回来时整张表是空的, 那时每一行都显示工具名, 不显示错的中文。
 */
export function toolActionLabel(toolName: string, directory: ToolDirectory = {}) {
  return directory[toolName]?.title || toolName;
}

export function categoryOf(toolName: string, directory: ToolDirectory = {}): ToolCategory {
  const byName = TOOL_CATEGORIES.find((item) => item.tools.includes(toolName));
  if (byName) return { id: byName.id, label: byName.label };
  if (directory[toolName]?.read_only) return READ_TOOLS;
  return OTHER_TOOLS;
}

export function toolNameOf(events: RunEvent[]) {
  const named = events.find((event) => event.payload.tool_name);
  return stringValue(named?.payload.tool_name) || "工具";
}

export function groupToolEvents(events: RunEvent[]) {
  const groups: Array<{ id: string; events: RunEvent[] }> = [];
  for (const event of events) {
    if (
      !event.kind.startsWith("tool_") &&
      !event.kind.startsWith("approval_") &&
      event.kind !== "policy_resolved"
    )
      continue;
    const key =
      event.invocation_id ?? event.tool_call_id ?? `tool-${event.payload.tool_name ?? event.sequence}`;
    let group = groups.find((item) => item.id === key);
    if (!group && event.invocation_id) {
      const name = stringValue(event.payload.tool_name);
      group = groups.find(
        (item) =>
          item.events.every((candidate) => !candidate.invocation_id) &&
          !item.events.some(
            (candidate) => candidate.kind === "tool_completed" || candidate.kind === "tool_cancelled",
          ) &&
          stringValue(item.events[0]?.payload.tool_name) === name,
      );
      if (group) group.id = event.invocation_id;
    }
    if (!group) {
      group = { id: key, events: [] };
      groups.push(group);
    }
    group.events.push(event);
  }
  return groups;
}

/**
 * 把一组工具活动收成一行。
 *
 * 写的是模型已经发出的**结构化调用请求**，不是自然语言里一句“准备干嘛”。请求可能仍在
 * 排队或随后被安全策略拒绝；真实状态由同一组里的后续事件决定。
 *
 * 名字一律取后端目录里的 title (`toolActionLabel`), 不在前端另写一套动词表。
 *
 * **多次调用只报次数, 不摆目标**: 摆第一个再跟一句"等 N 项", 等于让一个随机挑出来的
 * 文件占满整行, 而它并不比另外几个更值得看。要看是哪几个就展开。
 */
export function summariseTools(groups: ToolGroup[], directory: ToolDirectory = {}): ToolSummary {
  const names = [...new Set(groups.map((group) => toolNameOf(group.events)))];
  const labels = names.map((name) => toolActionLabel(name, directory));
  // 目录里没有这个名字时 toolActionLabel 退回工具名本身 —— 那不是一个可信的说法。
  const known = labels.every((label, index) => label !== names[index]);
  const count = groups.length;
  if (!known) return { text: "工具调用", count };

  // 一组里混了好几种工具 (同属一个动作才会被并到一起): 把种类列出来。
  if (names.length > 1) return { text: labels.join(" / "), count };

  const label = labels[0];
  // shell_run 的命令详情放在展开后的调用行里；组级摘要只说明动作，避免把整条命令
  // （或一条很长的管道）塞进折叠标题。这里使用稳定的界面文案，不暴露后端 title 的
  // 实现细节（当前 title 是“执行 Shell 命令”）。
  if (names[0] === "Bash" || names[0] === "shell_run") return { text: "执行命令", count };
  // 其余一律只给动作名: 具体读了哪个文件展开才看得到。收起时摆一个路径, 等于让一个
  // 随机挑出来的文件占满整行, 而它并不比另外几个更值得看。
  return { text: label, count };
}

/** 这次调用有没有真的执行过 —— 与"执行了然后失败了"是两回事。 */
export function wasExecuted(completed: RunEvent | undefined) {
  return !completed || completed.payload.executed !== false;
}

/** 后端给的是数组 (顺序稳定, 便于诊断), 展示要的是按名字查 —— 转一次即可。 */
export function indexTools(items: ToolDirectoryItem[]): ToolDirectory {
  return Object.fromEntries(
    items.map((item) => [item.name, { title: item.title, read_only: item.read_only }]),
  );
}
