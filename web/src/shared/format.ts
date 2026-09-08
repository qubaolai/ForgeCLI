/** 把数字与路径变成给人看的短字符串。
 *
 * 纯函数, 不认识任何一个功能 —— 认识了就该搬进那个功能自己的模块。
 */

export function formatDelay(milliseconds: number) {
  const seconds = Math.round(milliseconds / 1000);
  if (seconds < 60) return `${seconds} 秒后重试`;
  return `${Math.round(seconds / 60)} 分钟后重试`;
}

export function shortPath(path: string) {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.at(-1) || path;
}

/** 一段耗时的短写法。秒以下不显示小数, 分钟以上不显示秒。 */
export function formatElapsed(milliseconds: number) {
  return milliseconds >= 1000 ? `${(milliseconds / 1000).toFixed(1)}s` : `${Math.round(milliseconds)}ms`;
}
