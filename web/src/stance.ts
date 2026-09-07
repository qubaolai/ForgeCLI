/** 会话姿态的两个轴与它们的常用组合。
 *
 * 轴的取值来自后端 domain/intents; 这里只有给人看的名字与一句提示。
 */



/**
 * 姿态是两个独立的轴: 围栏允许什么(隔离), 以及什么时候要人点头(审批)。
 *
 * 早先这里是四档预设排成一条线, 于是"强隔离 + 全自动"和"弱隔离 + 每步都问"这两种
 * 组合根本选不出来 —— 它们在那条线上没有位置。
 */
export const sandboxOptions = [
  { value: "read_only", label: "只读", hint: "工作区不可写，只能看" },
  { value: "workspace_write", label: "工作区可写", hint: "能改文件，不能联网" },
  { value: "full_access", label: "完全访问", hint: "能改文件，也能联网" },
];

export const approvalOptions = [
  { value: "always", label: "每次确认", hint: "Shell 命令都要你点头" },
  { value: "auto", label: "围栏内自动", hint: "越界的才问你" },
  { value: "never", label: "不再确认", hint: "围栏就是全部边界" },
];

/** 常用组合的名字。选中其中一个等于同时设两个轴。 */

/** 常用组合的名字。选中其中一个等于同时设两个轴。 */
export const modePresets = [
  { sandbox: "read_only", approval: "always", label: "Plan", hint: "只出方案，先评审再动手" },
  { sandbox: "workspace_write", approval: "always", label: "Accept Edits", hint: "自动接受文件编辑" },
  { sandbox: "workspace_write", approval: "auto", label: "Auto", hint: "自动执行，风险操作仍需审批" },
  { sandbox: "full_access", approval: "never", label: "Full Access", hint: "打断最少，权限最大" },
];

export type Stance = { sandbox: string; approval: string };

/** 命中预设就用预设名, 否则把两个轴拼出来 —— 不编一个不存在的档位名。 */

/** 命中预设就用预设名, 否则把两个轴拼出来 —— 不编一个不存在的档位名。 */
export function stanceLabel(stance: Stance) {
  const preset = modePresets.find(
    (item) => item.sandbox === stance.sandbox && item.approval === stance.approval,
  );
  if (preset) return preset.label;
  const sandbox = sandboxOptions.find((item) => item.value === stance.sandbox);
  const approval = approvalOptions.find((item) => item.value === stance.approval);
  return `${sandbox?.label ?? stance.sandbox} · ${approval?.label ?? stance.approval}`;
}
