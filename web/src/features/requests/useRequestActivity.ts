/** 订阅在途请求。界面据此显示进度条与"还在等"的提醒。
 *
 * loading 完全跟着**真实的请求**走: `api()` 在发出 fetch 之前把计数加一, 拿到响应
 * (或抛错) 之后减一。所以它不会先于请求出现, 也不会在响应回来之后还留着 —— 界面
 * 自己另记一个标志的话, 这两件事迟早对不上。
 */

import { useSyncExternalStore } from "react";
import { requestActivity, subscribeRequests } from "../../api/client";

export function useRequestActivity() {
  const activity = useSyncExternalStore(subscribeRequests, requestActivity);
  return { ...activity, busy: activity.pending > 0 };
}
