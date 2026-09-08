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

/**
 * token 数的显示口径: 不足 1000 给整数, 到了 1000 换成 k。
 *
 * 一位小数就够: 这个数字是拿来判断量级的 (这轮烧得多不多), 不是拿来对账的 ——
 * 要对账得看供应商账单, 而那里的口径本来就与本地估算不同。
 * 尾随的 .0 去掉: `1.0k` 看起来像是精确到百位, 其实不是。
 */
export function formatTokens(value: number) {
  if (value < 1000) return String(Math.round(value));
  return `${(value / 1000).toFixed(1).replace(/\.0$/, "")}k`;
}
