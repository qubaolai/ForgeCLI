/** 待答队列只有一条, 卡片按 kind 分支 (ADR-0043 决策 3, 决策 11)。 */

import { ApprovalCard } from "@/features/humanInteraction/ApprovalCard";
import { QuestionCard } from "@/features/humanInteraction/QuestionCard";
import type { ResolvePrompt } from "@/features/humanInteraction/QuestionCard";
import { approvalOf } from "@/shared/lib/approval";
import type { Prompt } from "@/shared/lib/approval";

/** 待答队列只有一条, 卡片按 kind 分支 (ADR-0043 决策 11)。 */
export function PromptCard({ prompt, onResolve }: { prompt: Prompt; onResolve: ResolvePrompt }) {
  if (prompt.kind === "question") return <QuestionCard prompt={prompt} onResolve={onResolve} />;
  return (
    <ApprovalCard
      approval={approvalOf(prompt)}
      onResolve={(id, choice, text) => {
        void onResolve(id, choice, text).catch(() => undefined);
      }}
    />
  );
}
