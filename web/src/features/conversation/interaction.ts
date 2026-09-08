/** 键盘与滚动上, 什么时候算用户表达了意图。
 *
 * 两个判据都是纯函数, 所以不住在组件里 —— 它们要能被单独测。
 */

export function shouldSendOnEnter(
  key: string,
  shiftKey: boolean,
  isComposing: boolean,
  compositionActive: boolean,
) {
  return key === "Enter" && !shiftKey && !isComposing && !compositionActive;
}

export function shouldAutoFollow(
  scrollHeight: number,
  scrollTop: number,
  clientHeight: number,
  threshold = 140,
) {
  return scrollHeight - scrollTop - clientHeight <= threshold;
}
