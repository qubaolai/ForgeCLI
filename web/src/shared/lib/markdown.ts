export type MarkdownBlock =
  | { kind: "code"; language: string; text: string }
  | { kind: "heading"; level: number; text: string }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "quote"; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "rule" };

const ORDERED_ITEM = /^\s*\d+[.)]\s+(.+)$/;
const UNORDERED_ITEM = /^\s*[-+*]\s+(.+)$/;

export function parseMarkdown(content: string): MarkdownBlock[] {
  const lines = content.replaceAll("\r\n", "\n").split("\n");
  const blocks: MarkdownBlock[] = [];
  let index = 0;
  // 每轮必须推进 index。这不是洁癖: 解析器跑在流式渲染路径上, 一次不推进就是一个
  // 无限循环 + 无限 push, 标签页会被 OOM 杀掉, 用户连页面都关不掉。
  let previous = -1;
  while (index < lines.length) {
    if (index === previous) {
      index += 1;
      continue;
    }
    previous = index;
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }
    const fence = line.match(/^```([^`]*)$/);
    if (fence) {
      const body: string[] = [];
      index += 1;
      while (index < lines.length && !lines[index].startsWith("```")) body.push(lines[index++]);
      if (index < lines.length) index += 1;
      blocks.push({ kind: "code", language: fence[1].trim(), text: body.join("\n") });
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      blocks.push({ kind: "heading", level: heading[1].length, text: heading[2] });
      index += 1;
      continue;
    }
    if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
      blocks.push({ kind: "rule" });
      index += 1;
      continue;
    }
    // 用"能取出内容"作为进入条件, 而不是"看着像列表标记": 流式输出必然经过 `1. ` 这种
    // 标记已到达, 内容还没到的中间态, 宽进严出会让这一轮什么都消费不掉。
    const ordered = ORDERED_ITEM.test(line);
    const unordered = !ordered && UNORDERED_ITEM.test(line);
    if (ordered || unordered) {
      const items: string[] = [];
      const expression = ordered ? ORDERED_ITEM : UNORDERED_ITEM;
      while (index < lines.length) {
        const match = lines[index].match(expression);
        if (match) {
          items.push(match[1]);
          index += 1;
          continue;
        }
        if (items.length && /^\s{2,}\S/.test(lines[index])) {
          items[items.length - 1] += ` — ${lines[index].trim()}`;
          index += 1;
          continue;
        }
        break;
      }
      blocks.push({ kind: "list", ordered, items });
      continue;
    }
    if (/^\s*>/.test(line)) {
      const quote: string[] = [];
      while (index < lines.length && /^\s*>/.test(lines[index]))
        quote.push(lines[index++].replace(/^\s*>\s?/, ""));
      blocks.push({ kind: "quote", text: quote.join("\n") });
      continue;
    }
    const paragraph: string[] = [line];
    index += 1;
    while (index < lines.length && lines[index].trim() && !startsBlock(lines[index]))
      paragraph.push(lines[index++]);
    blocks.push({ kind: "paragraph", text: paragraph.join("\n") });
  }
  return blocks;
}

function startsBlock(line: string) {
  return /^```|^#{1,6}\s+|^\s*(?:\d+[.)]|[-+*])\s+|^\s*>|^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line);
}
