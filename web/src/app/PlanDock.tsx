/** 右侧计划栏, 连同它左边那条可以拖动的分隔线。
 *
 * 宽度由 App 持有: 网格布局要用它算列宽 (--context-width), 所以它不能只住在这里。
 */

import type { PointerEvent as ReactPointerEvent } from "react";
import { PlanningPanel } from "@/features/planning/PlanningPanel";
import type { PlanIndexView, Planning } from "@/types/session";

const MIN_WIDTH = 260;
const MAX_WIDTH = 680;

export function PlanDock({
  width,
  onWidth,
  onClose,
  planning,
  index,
  onResolve,
  onActivate,
}: {
  width: number;
  onWidth: (width: number) => void;
  onClose: () => void;
  planning: Planning;
  index: PlanIndexView | null;
  onResolve: (decision: string) => void;
  onActivate: (planId: string) => void;
}) {
  // 拖动期间在 document 上听, 不在分隔线上: 鼠标走得比重绘快, 光标一旦离开那条 5px
  // 的线, 挂在它身上的监听就再也收不到移动了。
  function beginResize(event: ReactPointerEvent<HTMLDivElement>) {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = width;
    const move = (moveEvent: PointerEvent) =>
      onWidth(Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startWidth + startX - moveEvent.clientX)));
    const stop = () => {
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerup", stop);
    };
    document.addEventListener("pointermove", move);
    document.addEventListener("pointerup", stop);
  }

  return (
    <>
      <div className="context-resizer" onPointerDown={beginResize} title="拖动调整计划栏宽度" />
      <aside className="context-panel">
        <div className="panel-heading">
          <strong>计划</strong>
          <div>
            <button
              onClick={() => window.open("/api/v1/planning/markdown", "_blank", "noopener,noreferrer")}
              disabled={!planning.plan}
            >
              打开 Markdown
            </button>
            <button onClick={onClose} aria-label="隐藏计划">
              ×
            </button>
          </div>
        </div>
        <PlanningPanel planning={planning} index={index} onResolve={onResolve} onActivate={onActivate} />
      </aside>
    </>
  );
}
