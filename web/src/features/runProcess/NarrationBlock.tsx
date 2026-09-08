/** 一次模型调用说的话。 */

import type { RunEvent } from "@/shared/lib/run/events";
import { stringValue } from "@/shared/lib/run/events";
import { Markdown } from "@/shared/ui/Markdown";

/**
 * 一次模型调用说的话。**总是可见**, 不折叠。
 *
 * 它可能是过程叙述 ("我先看一下目录"), 也可能是最终回答 —— 在这里不区分, 因为区分
 * 需要等调用收尾, 而那时字已经流完了。
 */
export function NarrationBlock({
  events,
  output,
  terminal,
}: {
  events: RunEvent[];
  output: string;
  terminal: boolean;
}) {
  const completed = events.find((event) => event.kind === "model_completed");
  const failed = events.find((event) => event.kind === "model_failed");
  const reasoning = [...events].reverse().find((event) => event.kind === "model_reasoning_status");
  const thinking = stringValue(reasoning?.payload.status) === "started";

  if (failed) {
    return (
      <p className="run-flow-error">
        {stringValue(failed.payload.message) || stringValue(failed.payload.error_kind)}
      </p>
    );
  }
  if (!output.trim()) {
    // 供应商异常路径可能只有 turn_failed，没有 model_failed/model_completed。此时本轮已经
    // 结束，不能让先前的 reasoning_started 继续显示成“思考中”。错误由终态详情展示。
    if (terminal) return null;
    // 还没吐字。思考与生成分开说 —— 思考可能持续很久且一个字都不吐, 看起来像卡住了。
    return completed ? null : <p className="run-flow-waiting">{thinking ? "思考中…" : "生成中…"}</p>;
  }
  return (
    <div className="run-flow-text">
      <Markdown content={output} />
    </div>
  );
}
