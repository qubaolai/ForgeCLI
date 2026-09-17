/** 会话模式 (ADR-0055 决策 1, ADR-0058): 与 Claude Code 同一组, 六档.
 *
 * 模式列表**不在这里写死**, 由 GET /modes 给: 名字, 说明都是后端 MODE_OPTIONS 的事实,
 * 这里再写一份就是第二份定义 —— 而且 Bypass Permissions 在不在列表里取决于 forge 启动时
 * 带没带 --allow-full-access, 那是页面自己不知道的事.
 */

/** 会话快照里的 `mode`: 模式名 (default / plan / accept_edits / bypass_permissions / dont_ask / auto). */
export type Stance = string;

/** GET /modes 的一项. `value` 是 POST /mode 要发的模式名. */
export type ModePreset = {
  value: string;
  label: string;
  hint: string;
};

/** 当前模式对应的菜单项; 不在菜单里 (dont_ask) 时为 undefined. */
export function presetOf(stance: Stance, presets: ModePreset[]) {
  return presets.find((item) => item.value === stance);
}

/** 命中菜单项就用它的名字, 否则原样显示模式名 —— 不编一个不存在的档位名. */
export function stanceLabel(stance: Stance, presets: ModePreset[]) {
  return presetOf(stance, presets)?.label ?? stance;
}
