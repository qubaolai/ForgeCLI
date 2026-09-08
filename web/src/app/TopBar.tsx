/** 顶栏: 品牌, 当前项目, 事件流连接状态, 计划栏开关, 设置入口。 */

import { ChevronIcon, GearIcon, PlanIcon } from "@/shared/ui/icons";
import { formatDelay, shortPath } from "@/shared/format";
import { connectionCopy } from "@/features/runEvents/useRunEvents";
import type { ConnectionState } from "@/features/runEvents/useRunEvents";
import type { Project } from "@/types/session";

export function TopBar({
  activeProject,
  pickerOpen,
  onTogglePicker,
  connection,
  retryDelay,
  planOpen,
  planProposed,
  onTogglePlan,
  onOpenSettings,
}: {
  activeProject?: Project;
  pickerOpen: boolean;
  onTogglePicker: () => void;
  connection: ConnectionState;
  retryDelay: number;
  planOpen: boolean;
  /** 有计划在等评审: 计划栏按钮上挂一个点。 */
  planProposed: boolean;
  onTogglePlan: () => void;
  onOpenSettings: () => void;
}) {
  return (
    <header className="topbar">
      <div className="top-brand">
        <span className="forge-mark small">F</span>
        <strong>Forge</strong>
      </div>
      <button
        className="project-picker"
        onClick={onTogglePicker}
        aria-expanded={pickerOpen}
        title={activeProject?.primary_workspace_root}
      >
        <span>{activeProject ? shortPath(activeProject.primary_workspace_root) : "选择项目"}</span>
        <ChevronIcon className="picker-caret" />
      </button>
      <span className={`connection ${connection}`} title={connectionCopy[connection].hint}>
        <i />
        <span className="connection-label">{connectionCopy[connection].label}</span>
        {retryDelay > 0 && <em>{formatDelay(retryDelay)}</em>}
      </span>
      <button
        className={`plan-toggle ${planOpen ? "active" : ""}`}
        onClick={onTogglePlan}
        aria-pressed={planOpen}
        aria-label={planOpen ? "隐藏计划" : "显示计划"}
      >
        <PlanIcon />
        {planProposed && <i className="plan-badge" title="有计划待评审" />}
      </button>
      <button className="icon-button" onClick={onOpenSettings} aria-label="设置">
        <GearIcon />
      </button>
    </header>
  );
}
