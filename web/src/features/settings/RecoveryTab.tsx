/** 恢复页: 未收尾的事务, 恢复点列表, 以及撤销最近一次。 */

import { useState } from "react";
import type { Administration } from "@/features/administration/useAdministration";
import type { Checkpoint } from "@/types/admin";

export function RecoveryTab({ admin }: { admin: Administration }) {
  return (
    <>
      <h3>恢复层状态</h3>
      <div className="admin-row">
        <div>
          <strong>恢复点总数 {admin.recovery?.checkpoint_count ?? 0}</strong>
          <small>
            {admin.recovery?.pending.length
              ? `${admin.recovery.pending.length} 个未收尾事务, 可能已发生部分修改`
              : "没有未收尾的事务"}
          </small>
        </div>
        <button onClick={admin.undoLatest} disabled={!admin.checkpoints.length}>
          撤销最近一次
        </button>
      </div>
      {admin.recovery?.pending.map((item) => (
        <div className="admin-row warning-row" key={item.checkpoint_id}>
          <div>
            <strong>{item.checkpoint_id}</strong>
            <small>
              {item.status} · {item.created_at}
            </small>
          </div>
          <button onClick={() => admin.restoreCheckpoint(item.checkpoint_id)}>恢复</button>
        </div>
      ))}
      <h3>恢复点</h3>
      {admin.checkpoints.length ? (
        admin.checkpoints.map((checkpoint) => (
          <CheckpointRow
            key={checkpoint.checkpoint_id}
            checkpoint={checkpoint}
            onRestore={admin.restoreCheckpoint}
            onPreview={admin.previewCheckpoint}
          />
        ))
      ) : (
        <p className="empty-copy">当前工作区没有恢复点。</p>
      )}
    </>
  );
}

function CheckpointRow({
  checkpoint,
  onRestore,
  onPreview,
}: {
  checkpoint: Checkpoint;
  onRestore: (id: string) => void;
  onPreview: (id: string) => Promise<string>;
}) {
  const [preview, setPreview] = useState("");
  return (
    <div className="admin-row checkpoint-row">
      <div>
        <strong>{checkpoint.checkpoint_id}</strong>
        <small>
          {checkpoint.status} · {checkpoint.snapshot_strategy} · {checkpoint.created_at}
        </small>
        {preview && <pre className="checkpoint-preview">{preview}</pre>}
      </div>
      <div className="row-actions">
        <button
          onClick={() => {
            if (preview) {
              setPreview("");
              return;
            }
            onPreview(checkpoint.checkpoint_id).then(setPreview);
          }}
        >
          {preview ? "收起" : "预览"}
        </button>
        <button onClick={() => onRestore(checkpoint.checkpoint_id)}>恢复</button>
      </div>
    </div>
  );
}
