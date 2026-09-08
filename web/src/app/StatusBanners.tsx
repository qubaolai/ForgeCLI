/** 顶部三条横幅: 请求进度, "还在等"的提醒, 以及错误。
 *
 * 前两条都来自请求客户端的统一记账 (shared/api/client), 界面自己不记 loading。
 */

import { SLOW_REQUEST_MS } from "@/shared/api/client";
import type { RequestActivity } from "@/shared/api/client";

export function StatusBanners({
  requests,
  error,
  onDismissError,
}: {
  requests: RequestActivity & { busy: boolean };
  error: string;
  onDismissError: () => void;
}) {
  return (
    <>
      {requests.busy && <div className="request-bar" role="progressbar" aria-label="正在请求" />}
      {requests.slow.length > 0 && (
        <div className="slow-banner" role="status">
          接口响应已超过 {Math.round(SLOW_REQUEST_MS / 1000)} 秒，仍在等待：{requests.slow.join("、")}
        </div>
      )}
      {error && (
        <div className="error-banner workspace-error" role="alert">
          <span>{error}</span>
          <button onClick={onDismissError} aria-label="关闭提示">
            ×
          </button>
        </div>
      )}
    </>
  );
}
