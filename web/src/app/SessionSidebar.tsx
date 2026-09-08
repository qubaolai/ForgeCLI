/** 左栏: 会话列表与当前项目的工作区目录。
 *
 * 一行 = 打开 + 删除两个按钮。整行不能是一个 button —— 嵌套 button 无效。
 */

import type { Project, Session } from "@/types/session";

export function SessionSidebar({
  sessions,
  currentSessionId,
  busy,
  activeProject,
  onCreate,
  onResume,
  onRequestDelete,
}: {
  sessions: Session[];
  currentSessionId: string | null;
  /** 有请求在跑时不许切走, 也不许删。 */
  busy: boolean;
  activeProject?: Project;
  onCreate: () => void;
  onResume: (sessionId: string) => void;
  onRequestDelete: (sessionId: string) => void;
}) {
  return (
    <aside className="sidebar">
      <div className="sidebar-title">
        <span>会话</span>
        <button onClick={onCreate}>＋</button>
      </div>
      <nav className="session-list">
        {sessions.map((session) => (
          <div
            className={`session-row ${session.session_id === currentSessionId ? "active" : ""}`}
            key={session.session_id}
          >
            <button
              className="session-open"
              onClick={() => onResume(session.session_id)}
              disabled={busy && session.session_id !== currentSessionId}
            >
              <strong title={session.title || "未命名会话"}>{session.title || "未命名会话"}</strong>
              <small>{session.updated_at?.slice(0, 16).replace("T", " ")}</small>
            </button>
            <button
              className="session-delete"
              title="删除会话"
              aria-label={`删除会话 ${session.title || session.session_id}`}
              disabled={busy}
              onClick={() => onRequestDelete(session.session_id)}
            >
              ×
            </button>
          </div>
        ))}
        {!sessions.length && <p className="empty-copy">发送第一条消息后，会话会出现在这里。</p>}
      </nav>
      <div className="workspace-roots">
        <span>工作区</span>
        {activeProject?.workspace_roots.map((root) => (
          <small key={root} title={root}>
            ● {root}
          </small>
        ))}
      </div>
    </aside>
  );
}
