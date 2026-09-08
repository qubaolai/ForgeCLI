/** 请求客户端。独立维护 (ADR-0048 决策 5): CSRF 令牌, 401 的说法与错误体解包只该有
 * 一份, 而它们与任何一个功能都无关。
 *
 * 在途请求的计数也在这里: 三十多个调用点各自记一个 loading 标志的话, 漏掉的那几个
 * 不会报错 —— 只会让界面在某些操作上看起来卡住了。所以计数统一由这一层维护, 界面
 * 订阅它, 谁都不必自己记。
 */

export const STALE_SESSION = "本地会话已失效，请回到终端重新打开 Forge 启动链接。";

/** 超过这个时长就提醒一次"还在等"。改这一个数就改了全部接口。 */
export const SLOW_REQUEST_MS = 10000;

export type RequestActivity = {
  /** 在途请求数。 */
  pending: number;
  /** 已经超过 SLOW_REQUEST_MS 还没回来的请求, 给人看的短标签。 */
  slow: readonly string[];
};

let csrfToken = "";
let pending = 0;
const slow = new Map<number, string>();
const listeners = new Set<(activity: RequestActivity) => void>();
let snapshot: RequestActivity = { pending: 0, slow: [] };
let ticket = 0;

export function setCsrfToken(token: string) {
  csrfToken = token;
}

function publish() {
  // 每次换一个新对象: useSyncExternalStore 靠引用相等判断要不要重渲染。
  snapshot = { pending, slow: [...slow.values()] };
  for (const listener of listeners) listener(snapshot);
}

export function subscribeRequests(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function requestActivity(): RequestActivity {
  return snapshot;
}

/** 给人看的短标签: 方法 + 路径首段, 不带 id —— 提醒里要的是"哪件事慢", 不是完整 URL。 */
function label(path: string, method: string): string {
  const head = path.split("?")[0].split("/").filter(Boolean)[0] ?? path;
  return `${method} /${head}`;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body) headers.set("Content-Type", "application/json");
  const method = init?.method ?? "GET";
  if (method !== "GET") headers.set("X-CSRF-Token", csrfToken);

  const id = ++ticket;
  pending += 1;
  publish();
  // 只提醒, **不中止**: 请求已经到了服务端的话, 中止它并不能撤销那次写入 —— 一个
  // DELETE 可能已经把会话删掉了, 这时候报"超时失败"比说"还在等"更容易让人做错事。
  const remind = window.setTimeout(() => {
    slow.set(id, label(path, method));
    publish();
  }, SLOW_REQUEST_MS);

  try {
    const response = await fetch(`/api/v1${path}`, { ...init, headers });
    if (!response.ok) {
      // 服务重启后旧 cookie 不再有效，直接说清楚怎么恢复，而不是抛一个裸 401。
      if (response.status === 401) throw new Error(STALE_SESSION);
      let detail = `${response.status} ${response.statusText}`;
      try {
        const body = (await response.json()) as { detail?: string };
        detail = body.detail ?? detail;
      } catch {
        /* use HTTP status */
      }
      throw new Error(detail);
    }
    return (await response.json()) as T;
  } finally {
    window.clearTimeout(remind);
    slow.delete(id);
    pending -= 1;
    publish();
  }
}
