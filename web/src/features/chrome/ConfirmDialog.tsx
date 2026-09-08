/** 确认对话框。破坏性操作按下之前, 代价必须写在同一屏上。
 *
 * 与设置面板共用 `.modal-backdrop`: 遮罩层的观感只该有一份。Esc 与点遮罩都等于取消 ——
 * 唯一让它继续的方式是按那个按钮, 而这正是"不小心点到"与"确实要做"之间的区别。
 *
 * 执行期间对话框不关: 关掉它等于把"做完了没有"这件事藏起来, 而删除这类操作恰恰是
 * 用户最想看到进度的时候。
 */

import { useEscape } from "@/shared/hooks/useEscape";

export function ConfirmDialog({
  title,
  body,
  confirmLabel,
  busy,
  danger = true,
  onConfirm,
  onCancel,
}: {
  title: string;
  body: string;
  confirmLabel: string;
  /** 正在执行。按钮转成进行时文案并锁住, 避免重复提交。 */
  busy: boolean;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  // 执行中不响应 Esc: 请求已经发出去了, 关掉界面并不能收回它。
  useEscape(!busy, onCancel);
  return (
    <div
      className="modal-backdrop confirm-layer"
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-title"
      onMouseDown={(event) => {
        if (!busy && event.target === event.currentTarget) onCancel();
      }}
    >
      <section className="confirm-dialog">
        <h2 id="confirm-title">{title}</h2>
        <p>{body}</p>
        <footer>
          <button onClick={onCancel} disabled={busy}>
            取消
          </button>
          <button className={danger ? "danger" : "primary"} onClick={onConfirm} disabled={busy} autoFocus>
            {busy ? `${confirmLabel}中…` : confirmLabel}
          </button>
        </footer>
      </section>
    </div>
  );
}
