/** 请求客户端。独立维护 (ADR-0048 决策 5): CSRF 令牌, 401 的说法与错误体解包只该有
 * 一份, 而它们与任何一个功能都无关。
 */

let csrfToken = "";

export function setCsrfToken(token: string) {
  csrfToken = token;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body) headers.set("Content-Type", "application/json");
  if (init?.method && init.method !== "GET") headers.set("X-CSRF-Token", csrfToken);
  const response = await fetch(`/api/v1${path}`, { ...init, headers });
  if (!response.ok) {
    // 服务重启后旧 cookie 不再有效，直接说清楚怎么恢复，而不是抛一个裸 401。
    if (response.status === 401) throw new Error(STALE_SESSION);
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json() as { detail?: string };
      detail = body.detail ?? detail;
    } catch { /* use HTTP status */ }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const STALE_SESSION = "本地会话已失效，请回到终端重新打开 Forge 启动链接。";
