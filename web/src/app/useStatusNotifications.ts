/** 全局提示: 请求失败, 以及"这个请求还在等"。
 *
 * 用 antd 的 notification 而不是自绘横幅: 它自己管层级 (盖得住模态框)、堆叠与关闭。
 * 两条提示都带固定 key, 所以同一件事只会有一条, 状态消失时由这里销毁。
 */

import { useEffect } from "react";
import { App } from "antd";
import { SLOW_REQUEST_MS } from "@/shared/api/client";
import type { RequestActivity } from "@/shared/api/client";

export function useStatusNotifications({
  requests,
  error,
  onDismissError,
}: {
  requests: RequestActivity;
  error: string;
  onDismissError: () => void;
}) {
  const { notification } = App.useApp();
  const slowRequests = requests.slow.join("、");

  useEffect(() => {
    if (!error) {
      notification.destroy("workspace-error");
      return;
    }
    notification.error({
      key: "workspace-error",
      title: "请求失败",
      description: error,
      duration: 0,
      onClose: onDismissError,
      role: "alert",
    });
  }, [notification, error, onDismissError]);

  useEffect(() => {
    if (!slowRequests) {
      notification.destroy("slow-request");
      return;
    }
    notification.warning({
      key: "slow-request",
      title: "请求仍在处理中",
      description: `接口响应已超过 ${Math.round(SLOW_REQUEST_MS / 1000)} 秒，仍在等待：${slowRequests}`,
      duration: 0,
      role: "status",
    });
  }, [notification, slowRequests]);
}
