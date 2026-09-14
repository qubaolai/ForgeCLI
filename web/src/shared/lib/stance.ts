/** 会话模式: 后端的两个轴与给人选的预设.
 *
 * 轴的取值来自后端 domain/intents. 预设列表**不在这里写死**, 由 GET /modes 给
 * (ADR-0052): 名字, 说明, 以及每个预设对应的两个轴都是后端 MODE_PRESETS / PRESET_NAMES
 * 的事实, 这里再写一份就是第二份定义 —— 而且 Full Access 在不在列表里取决于 forge
 * 启动时带没带 --allow-full-access, 那是页面自己不知道的事.
 */

/** 会话快照里的 `mode`: 隔离档 x 审批档. 界面不按轴调, 只用它对回预设. */
export type Stance = { sandbox: string; approval: string };

/** GET /modes 的一项. `value` 是 POST /mode 要发的预设名. */
export type ModePreset = {
  value: string;
  label: string;
  hint: string;
  sandbox: string;
  approval: string;
};

/** 当前两个轴恰好是哪个预设; 不是任何预设时为 undefined. */
export function presetOf(stance: Stance, presets: ModePreset[]) {
  return presets.find((item) => item.sandbox === stance.sandbox && item.approval === stance.approval);
}

/** 命中预设就用预设名, 否则把两个轴拼出来 —— 不编一个不存在的档位名. */
export function stanceLabel(stance: Stance, presets: ModePreset[]) {
  return presetOf(stance, presets)?.label ?? `${stance.sandbox} · ${stance.approval}`;
}
