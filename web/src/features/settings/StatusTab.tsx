/** 状态页: 当前会话、模式、模型与可操作目录的只读快照。 */

import type { Administration } from "@/features/administration/useAdministration";

export function StatusTab({ admin }: { admin: Administration }) {
  const status = admin.statusView;
  return (
    <>
      <h3>会话状态</h3>
      <div className="status-grid">
        <div>
          <span>会话</span>
          <code>{status?.session_id || "-"}</code>
        </div>
        <div>
          <span>模式</span>
          <code>{status?.mode || "-"}</code>
        </div>
        <div>
          <span>当前模型</span>
          <code>{status?.model || "未设置"}</code>
        </div>
        <div>
          <span>最近事件</span>
          <code>{status?.last_event_id || "-"}</code>
        </div>
        <div>
          <span>运行中</span>
          <code>{status?.busy ? "是" : "否"}</code>
        </div>
      </div>
      <h3>可操作目录</h3>
      {(status?.workspace_roots ?? []).map((root) => (
        <div className="admin-row" key={root}>
          <div>
            <strong>{root}</strong>
          </div>
        </div>
      ))}
    </>
  );
}
