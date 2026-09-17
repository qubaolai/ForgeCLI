/**
 * 审批卡片的派生逻辑 (ADR-0055 决策 4 / 6)。
 *
 * 和 runModel.ts 同一个分法: 纯函数住在 .ts 里, .tsx 只渲染。
 *
 * **这里不做安全判断, 只做映射。** 每一档都由后端已经算好的字段决定: 界面重新推导
 * 一次"这次危不危险", 就是把裁决逻辑复制到了一个没人测的地方。
 */

/** 卡片上的一个建议按钮: 后端 `PermissionUpdate` 的展示形状, `value` 原样回给通道。 */
export type ApprovalSuggestion = {
  value: string;
  type: string;
  destination: string;
  label: string;
  rules: string[];
  directories: string[];
  mode: string;
};

/** `detail`: security/interactive.py 组的那一份, 终端与 Web 读同一份。 */
export type ApprovalView = {
  tool_name: string;
  tool_use_id: string;
  user_facing_name: string;
  activity: string;
  summary: string;
  input: Record<string, unknown>;
  message: string;
  reason: string;
  blocked_path: string;
  destructive: boolean;
  suggestions: ApprovalSuggestion[];
};

export type PromptChoice = { value: string; label: string; detail: string };

/**
 * 后端待答队列里的一条提示 (ADR-0043 决策 3)。
 *
 * 审批与模型提问共用这一条队列, `kind` 决定渲染哪个分支。`detail` 是**不透明的** ——
 * 审批那侧装的就是 ApprovalView, 通道原样搬运。
 */
export type Prompt = {
  prompt_id: string;
  kind: "approval" | "question";
  title: string;
  body: string;
  choices: PromptChoice[];
  free_text: boolean;
  selection_mode?: "single" | "multiple";
  recommended_option_id?: string;
  allow_skip?: boolean;
  detail: Record<string, unknown>;
};

export type Approval = { approval_id: string; view: ApprovalView; choices: PromptChoice[] };

/** 把一条审批提示还原成卡片逻辑要的形状: 一次投影, 不重建。 */
export function approvalOf(prompt: Prompt): Approval {
  const detail = prompt.detail as Partial<ApprovalView>;
  return {
    approval_id: prompt.prompt_id,
    choices: prompt.choices,
    view: {
      tool_name: detail.tool_name ?? "",
      tool_use_id: detail.tool_use_id ?? "",
      user_facing_name: detail.user_facing_name ?? detail.tool_name ?? "",
      activity: detail.activity ?? "",
      summary: detail.summary ?? "",
      input: detail.input ?? {},
      message: detail.message ?? prompt.body,
      reason: detail.reason ?? "",
      blocked_path: detail.blocked_path ?? "",
      destructive: Boolean(detail.destructive),
      suggestions: detail.suggestions ?? [],
    },
  };
}

export type Severity = "calm" | "caution" | "critical";

/** 三档危险程度: 后端标了不可逆就是 critical, 安全检查与工作目录之外是 caution。 */
export function severityOf(approval: Approval): Severity {
  const view = approval.view;
  if (view.destructive) return "critical";
  if (view.reason.startsWith("safety_check") || view.reason.startsWith("working_dir")) return "caution";
  return "calm";
}

/** 卡片标题: 工具自己给的一句说明, 没有就用工具名。 */
export function headlineOf(approval: Approval): string {
  const view = approval.view;
  return view.activity || view.summary || view.user_facing_name || view.tool_name;
}

/** 值太长或多行的入参放代码块, 其余一行一项。 */
export function isLongValue(value: unknown): boolean {
  if (typeof value !== "string") return false;
  return value.includes("\n") || value.length > 120;
}

export function inputEntries(approval: Approval): Array<[string, string]> {
  return Object.entries(approval.view.input).map(([key, value]) => [
    key,
    typeof value === "string" ? value : JSON.stringify(value),
  ]);
}

/** "允许这一次"与"拒绝"之外的按钮: 后端建议了什么就画什么。 */
export function suggestionChoices(approval: Approval): PromptChoice[] {
  return approval.choices.filter((choice) => choice.value !== "yes" && choice.value !== "no");
}
