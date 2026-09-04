/**
 * 审批卡片的派生逻辑。
 *
 * 和 runModel.ts 同一个分法: 纯函数住在 .ts 里, .tsx 只渲染。这样它们能被
 * `node --test` 直接 import —— 审批是"改错了会出事"的那一类界面, 它的判据不该只有
 * 肉眼验收。
 *
 * **这里不做安全判断, 只做映射。** 每一档都由后端已经算好的字段决定: 界面重新推导
 * 一次"这次危不危险", 就是把裁决逻辑复制到了一个没人测的地方。
 */

export type ApprovalScript = {
  language: string;
  origin: string;
  path: string;
  /** 后端字段就叫 source。早先这里写成 content, 于是取到的一直是 undefined 且不报错。 */
  source: string;
};

export type ApprovalPreview = { path: string; content: string; truncated: boolean };

export type ApprovalTargetGroup = { label: string; paths: string[] };

export type ApprovalCount = { label: string; count: number };

export type ApprovalView = {
  mode: string;
  tool_name: string;
  workspace_roots: string[];
  raw_command: string;
  target_resolution: string;
  target_groups: ApprovalTargetGroup[];
  script_snapshots: ApprovalScript[];
  content_previews: ApprovalPreview[];
  counts: ApprovalCount[];
  unresolved_reason?: string | null;
  allowed_scopes: string[];
  learn_blocked_reason: string;
};

export type PromptChoice = { value: string; label: string; detail: string };

/**
 * 后端待答队列里的一条提示 (ADR-0043 决策 3)。
 *
 * 审批与模型提问共用这一条队列, `kind` 决定渲染哪个分支。`detail` 是**不透明的** ——
 * 审批那侧装的就是 `ApprovalView.to_payload()` 加一个 `mandatory` 键, 通道原样搬运。
 */
export type Prompt = {
  prompt_id: string;
  kind: "approval" | "question";
  title: string;
  body: string;
  choices: PromptChoice[];
  free_text: boolean;
  detail: Record<string, unknown>;
};

export type Approval = { approval_id: string; mandatory: boolean; view: ApprovalView };

/**
 * 把一条审批提示还原成卡片逻辑要的形状。
 *
 * 这是一次**投影而不是重建**: `detail` 本身就是后端那份 `ApprovalView.to_payload()`,
 * 这里只是把它和 `mandatory` 拆回两个字段。逐字段抄一遍就会多一处会漂的地方 ——
 * 而漂了不会报错, 只会让卡片上少一样东西。
 */
export function approvalOf(prompt: Prompt): Approval {
  const detail = prompt.detail as ApprovalView & { mandatory?: boolean };
  return {
    approval_id: prompt.prompt_id,
    mandatory: Boolean(detail.mandatory),
    view: detail,
  };
}

export type Severity = "calm" | "caution" | "critical";

/** 后端 target_groups / counts 的标签, 见 ApprovalView.target_groups。 */
const DELETE_LABEL = "删除";
const EXTERNAL_LABEL = "外部副作用";
const CAUTION_LABELS = ["写入", "移动", "网络"];

function countOf(view: ApprovalView, label: string) {
  const found = view.counts.find((item) => item.label === label);
  if (found) return found.count;
  // counts 是后端送的; 万一没送 (旧版本), 退回数 target_groups —— 但绝不退回 0,
  // 那会让一次删除看起来像只读。
  const group = view.target_groups.find((item) => item.label === label);
  return group ? group.paths.length : 0;
}

/**
 * 三档危险程度。
 *
 * 顺序即优先级, 且**只升不降**: 任何一条 critical 判据成立就是 critical, 后面的
 * caution 判据再宽也盖不回去。
 */
export function severityOf(approval: Approval): Severity {
  const view = approval.view;
  if (approval.mandatory) return "critical";
  if (view.unresolved_reason) return "critical";
  if (countOf(view, DELETE_LABEL) > 0) return "critical";
  if (countOf(view, EXTERNAL_LABEL) > 0) return "critical";
  if (CAUTION_LABELS.some((label) => countOf(view, label) > 0)) return "caution";
  return "calm";
}

export function canLearn(approval: Approval) {
  return approval.view.allowed_scopes.includes("workspace");
}

/**
 * "始终允许"旁边那行字。
 *
 * 可选时说清记住的**不是这行命令的字面量** —— 学习规则绑的是可执行文件身份加目标
 * 集合, 用户按字面理解就会以为换个参数也照样放行。
 *
 * 不可选时把后端给的理由原样端出来, 不自己编: 前端只看得到 allowed_scopes 少了一档,
 * 看不到少的是哪一条判据。
 */
export function learnHintOf(approval: Approval): string {
  if (canLearn(approval)) {
    return "记住的是这个可执行文件与这组参数结构, 不是这行命令的字面量。";
  }
  const reason = approval.view.learn_blocked_reason.trim();
  return reason ? `不能记成规则: ${reason}` : "这次不能记成规则。";
}

/**
 * 卡片标题: 一句话说清这次会发生什么。
 *
 * 全部由计数与两个标志位推出来, 没有按工具名写死的文案 —— 新增工具不必回来改这里,
 * 而写死的那张表迟早会漏掉一个。
 */
export function headlineOf(approval: Approval): string {
  const view = approval.view;
  if (approval.mandatory) return "这一步必须你亲自确认";
  if (view.unresolved_reason) return "目标范围无法提前确定";
  const deletes = countOf(view, DELETE_LABEL);
  if (deletes > 0) return `要删除 ${deletes} 个路径`;
  if (countOf(view, EXTERNAL_LABEL) > 0) return "有外部不可逆动作";
  const writes = countOf(view, "写入") + countOf(view, "移动");
  if (writes > 0) return `要改 ${writes} 个文件`;
  if (countOf(view, "网络") > 0) return "要访问网络";
  return "要跑一条只读命令";
}

/** 只读且目标封闭时不列目标清单: 命令与脚本用户已经逐字看过, 再列一遍读了哪些
 *  文件只是噪音; 沾上写/删/移动/网络, 或目标集合没封闭, 清单才有信息量。 */
export function isConsequential(approval: Approval) {
  const view = approval.view;
  if (view.unresolved_reason) return true;
  return view.target_groups.some(
    (group) => group.label !== "读取" && group.paths.length > 0,
  );
}

/** 有内容的目标分组; 空组不占位置。 */
export function shownTargetGroups(approval: Approval) {
  return approval.view.target_groups.filter((group) => group.paths.length > 0);
}

/**
 * 证据区哪几块默认展开。
 *
 * 收起的是**体量**, 不是事实的存在: 脚本正文与写入预览是用户批准的那个东西本身,
 * 所以只要有就默认展开; 目标清单是可以从上面那两样推出来的, 默认收起。
 */
export function defaultOpenSections(approval: Approval) {
  return {
    scripts: approval.view.script_snapshots.length > 0,
    previews: approval.view.content_previews.length > 0,
    targets: false,
  };
}
