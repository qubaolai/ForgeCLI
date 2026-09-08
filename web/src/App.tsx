import {
  CSSProperties,
  FormEvent,
  PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { ChevronIcon, GearIcon, PlanIcon } from "./icons";
import {
  appendRunEvent,
  failUnboundTurn,
  finishLocalTurn,
  isTerminalEvent,
  LocalTurn,
  newLocalTurn,
  restoreTurn,
  RunSnapshot,
  shouldAutoFollow,
  shouldSendOnEnter,
} from "./runModel";
import { api, setCsrfToken, SLOW_REQUEST_MS } from "./api/client";
import { useRequestActivity } from "./features/requests/useRequestActivity";
import { ConfirmDialog } from "./features/chrome/ConfirmDialog";
import { useAdministration } from "./features/administration/useAdministration";
import { useProjects } from "./features/projects/useProjects";
import { usePrompts } from "./features/humanInteraction/usePrompts";
import { useSettings } from "./features/settings/useSettings";
import { usePlanning } from "./features/planning/usePlanning";
import { type Stance } from "./stance";
import { shortPath, useEscape, formatDelay } from "./ui";
import { LocalTurnView, Message, RestoredTurnView } from "./features/conversation/Timeline";
import { ModelMenu } from "./features/chrome/ModelMenu";
import { ModeMenu } from "./features/chrome/ModeMenu";
import { ProjectPicker } from "./features/chrome/ProjectPicker";
import { PlanningPanel } from "./features/planning/PlanningPanel";
import { PromptCard } from "./features/humanInteraction/PromptCards";
import { SettingsPanel } from "./features/settings/SettingsPanel";
import { connectionCopy, useRunEvents } from "./features/runEvents/useRunEvents";
import { useResumePosition } from "./features/runEvents/resumePosition";
import type { Session, TranscriptEvent, TurnRunState } from "./types";

function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<TranscriptEvent[]>([]);
  const [restoredRuns, setRestoredRuns] = useState<LocalTurn[]>([]);
  const [localTurns, setLocalTurns] = useState<LocalTurn[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [stance, setStance] = useState<Stance>({ sandbox: "workspace_write", approval: "always" });
  const [error, setError] = useState("");
  const [showSettings, setShowSettings] = useState(false);
  const [showPlan, setShowPlan] = useState(false);
  const [planWidth, setPlanWidth] = useState(320);
  const [showProjectPicker, setShowProjectPicker] = useState(false);
  const [showJumpToBottom, setShowJumpToBottom] = useState(false);
  const [stopping, setStopping] = useState(false);
  // 正在等待确认的那个会话 id。删除不可撤销, 所以走一个必须显式按下的对话框。
  const [confirmDelete, setConfirmDelete] = useState("");
  // 在途请求由客户端统一记账 (api/client), 这里只订阅结果。
  const requests = useRequestActivity();
  const [deletingSession, setDeletingSession] = useState(false);
  const timelineRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const autoFollowRef = useRef(true);
  const composingRef = useRef(false);
  const announcedPlanRef = useRef("");
  // 当前会话, 给事件回调用。回调建立时闭包住的那个值会过期, 而过期的作用域判断比
  // 不判断更糟: 它会把新会话的事件当成外来的丢掉 (ADR-0048 决策 2)。
  const sessionScopeRef = useRef("");
  const refreshHandleRef = useRef(0);
  const resumePosition = useResumePosition();

  useEffect(() => {
    sessionScopeRef.current = currentSessionId ?? "";
  }, [currentSessionId]);

  const restoredByTurn = useMemo(() => {
    const live = new Set(localTurns.map((turn) => turn.turnId));
    return new Map(
      restoredRuns
        .filter((turn) => turn.turnId && !live.has(turn.turnId))
        .map((turn) => [turn.turnId as string, turn]),
    );
  }, [restoredRuns, localTurns]);

  // 每个功能自己持有状态与动作 (ADR-0048 决策 5)。App 只剩外壳: 对话正文, 布局,
  // 以及把这些功能接到一起。
  const projectsFeature = useProjects();
  const { activeProjectId } = projectsFeature;
  const promptsFeature = usePrompts(setError);
  const settingsFeature = useSettings(setError);
  const planningFeature = usePlanning(setError);
  const admin = useAdministration(setError, projectsFeature.load);
  const loadProjects = projectsFeature.load;
  const loadPrompts = promptsFeature.load;

  const configuredModels = useMemo(
    () => admin.providers.flatMap((provider) => provider.models.map((model) => `${provider.id}:${model.id}`)),
    [admin.providers],
  );

  const loadConversation = useCallback(async () => {
    const [sessionResult, runResult] = await Promise.all([
      api<{ current_session_id: string; current_session: Session; items: Session[] }>("/sessions"),
      api<TurnRunState | null>("/turns/current"),
    ]);
    setSessions(sessionResult.items);
    setCurrentSessionId(sessionResult.current_session_id);
    setStance(sessionResult.current_session.mode ?? { sandbox: "workspace_write", approval: "always" });
    // 服务端才是"是否还在跑"的真相源：刷新页面后按它恢复运行态。
    setBusy(runResult?.status === "running");
  }, []);

  /** 各功能各自去拉, 互不等待 (ADR-0048 决策 5)。
   *
   * 原先这四份绑在一个 `Promise.all` 里, 于是一个慢查询会把其余三份一起压住 —— 工具
   * 挂起时待答卡片也跟着出不来。现在只有会话与运行态还成对: 它们要一起决定"是否在跑"。
   */
  const loadWorkspace = useCallback(async () => {
    const report = (reason: Error) => setError(reason.message);
    void loadPrompts().catch(report);
    void settingsFeature.load().catch(report);
    void planningFeature.load().catch(report);
    await loadConversation();
    // 依赖各功能的 load 本身, 而不是功能对象: 对象每次 render 都是新的, 会让下面
    // 那个 effect 每渲染一次就重新拉一遍。
  }, [loadPrompts, settingsFeature.load, planningFeature.load, loadConversation]);

  const transcriptRequestRef = useRef(0);
  const loadTranscript = useCallback(
    async (sessionId: string) => {
      // 按请求代次 + 作用域双重校验 (ADR-0048 决策 5)。会话 A 的慢响应晚于会话 B 的
      // 到达时, 无条件写入会把 B 的正文和处理过程换成 A 的 —— 而页面上看不出异常。
      const version = ++transcriptRequestRef.current;
      const stale = () => version !== transcriptRequestRef.current;
      try {
        const result = await api<{ items: TranscriptEvent[] }>(`/sessions/${sessionId}/transcript`);
        if (stale()) return;
        setTranscript(result.items);
      } catch {
        if (stale()) return;
        setTranscript([]);
      }
      // 处理过程现在落盘 (sessions/<id>/runs.jsonl)，所以历史轮次也展得开。
      // 后端把落盘的与进程内还没收尾的那一轮合并后一起发。
      try {
        const runs = await api<{ items: RunSnapshot[]; session_id?: string; resume?: string }>("/runs");
        // 响应自报归属: 代次之外再核一次会话, 取消旧请求代替不了这一步 —— /runs 取的
        // 是"当前会话", 而请求发出到返回之间当前会话可能已经换了。
        if (stale() || (runs.session_id && runs.session_id !== sessionId)) return;
        setRestoredRuns(runs.items.map((item) => restoreTurn(item)));
        // 快照自带水位: 先应用快照, 再接水位之后的事件, 中间那一段不会掉 (ADR-0048 决策 2)。
        resumePosition.adopt(runs.resume);
      } catch {
        if (!stale()) setRestoredRuns([]);
      }
    },
    [resumePosition],
  );

  useEffect(() => {
    api<{ csrf_token: string; active_project_id: string | null }>("/bootstrap")
      .then((value) => {
        setCsrfToken(value.csrf_token);
      })
      .then(loadProjects)
      .catch((reason: Error) => setError(reason.message));
  }, [loadProjects]);

  useEffect(() => {
    if (!activeProjectId) return;
    loadWorkspace().catch((reason: Error) => setError(reason.message));
    admin.loadModels().catch(() => undefined);
    void admin.loadToolDirectory();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- admin 的动作每次 render 都是新的
  }, [activeProjectId, loadWorkspace]);

  useEffect(() => {
    if (currentSessionId) loadTranscript(currentSessionId);
  }, [currentSessionId, loadTranscript]);

  useEffect(() => {
    const theme = settingsFeature.settings.find((item) => item.key === "output.theme")?.value;
    if (theme === "dark" || theme === "light") {
      document.documentElement.dataset.theme = theme;
    }
  }, [settingsFeature.settings]);

  const refreshWorkspace = useCallback(() => {
    // 工作区刷新是 5 个并行请求；密集事件下不节流会把浏览器的连接数吃光。
    if (refreshHandleRef.current) return;
    refreshHandleRef.current = window.setTimeout(() => {
      refreshHandleRef.current = 0;
      loadWorkspace().catch(() => undefined);
    }, 300);
  }, [loadWorkspace]);

  useEffect(
    () => () => {
      if (refreshHandleRef.current) window.clearTimeout(refreshHandleRef.current);
    },
    [],
  );

  useEscape(
    showProjectPicker,
    useCallback(() => setShowProjectPicker(false), []),
  );
  useEscape(
    showSettings,
    useCallback(() => setShowSettings(false), []),
  );

  // 事件流自己管连接, 退避与合帧; 这里只说"到了之后干什么" (ADR-0048 决策 5)。
  const { connection, retryDelay, discardPending } = useRunEvents(activeProjectId, resumePosition, {
    // 事件缓冲跨会话共用: 重连补发时会带上切换之前那个会话的尾巴。两个会话都有
    // turn_0001, 不按归属丢掉就会叠进当前时间线 (ADR-0048 决策 2)。
    accepts: (event) => {
      const scope = sessionScopeRef.current;
      return !scope || !event.session_id || event.session_id === scope;
    },
    onBatch: (batch) => {
      setLocalTurns((items) => batch.reduce((carry, event) => appendRunEvent(carry, event), items));
    },
    onEvent: (event, flush) => {
      if (event.kind === "prompt_requested" || event.kind === "prompt_resolved") {
        void loadPrompts().catch((reason: Error) => setError(reason.message));
      }
      // 后端会在首个工具启动前连续发布整批 tool_queued，最后一条 queue_position=0。
      // 立刻提交完整批次，避免普通流式事件的 33ms 合并窗口把它吞到完成事件后面。
      if (event.kind === "tool_queued" && Number(event.payload.queue_position ?? 0) === 0) {
        flush();
      }
      if (isTerminalEvent(event)) {
        flush();
        setBusy(false);
        refreshWorkspace();
        window.setTimeout(() => syncFinishedTurn(event.turn_id), 60);
      }
      if (["approval_requested", "approval_resolved", "plan_proposed", "todo_updated"].includes(event.kind)) {
        refreshWorkspace();
      }
    },
    onResync: () => {
      // 位置已经被 hook 丢掉了; 这里负责重新取一次快照, 由快照带回新水位。
      const scope = sessionScopeRef.current;
      if (scope) void loadTranscript(scope).catch(() => undefined);
      refreshWorkspace();
    },
    onOpen: (reconnected) => {
      void loadPrompts().catch((reason: Error) => setError(reason.message));
      if (reconnected) {
        // 断线期间可能换了进程：项目、会话和运行态都要重新对齐。
        loadProjects().catch(() => undefined);
        refreshWorkspace();
      }
    },
    onError: setError,
  });

  useEffect(() => {
    if (!busy) setStopping(false);
  }, [busy]);

  useEffect(() => {
    // 计划待评审时决议按钮只在计划栏里，所以新提案必须自己弹出来一次。
    const plan = planningFeature.planning.plan;
    if (!plan || plan.status !== "proposed" || announcedPlanRef.current === plan.plan_id) return;
    announcedPlanRef.current = plan.plan_id;
    setShowPlan(true);
  }, [planningFeature.planning.plan]);

  useEffect(() => {
    const node = composerRef.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${Math.min(node.scrollHeight, 240)}px`;
  }, [message]);

  useEffect(() => {
    if (!autoFollowRef.current) return;
    const frame = window.requestAnimationFrame(() => {
      const timeline = timelineRef.current;
      if (timeline) timeline.scrollTop = timeline.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [transcript, localTurns]);

  async function syncFinishedTurn(turnId: string, attempt = 0) {
    try {
      const run = await api<TurnRunState | null>("/turns/current");
      if (run?.status === "running" && attempt < 6) {
        window.setTimeout(() => syncFinishedTurn(turnId, attempt + 1), 80 * (attempt + 1));
        return;
      }
      setLocalTurns((items) => finishLocalTurn(items, turnId, run?.response?.text, run?.error));
    } catch {
      if (attempt < 2) window.setTimeout(() => syncFinishedTurn(turnId, attempt + 1), 150);
    }
  }

  function handleTimelineScroll() {
    const timeline = timelineRef.current;
    if (!timeline) return;
    const follow = shouldAutoFollow(timeline.scrollHeight, timeline.scrollTop, timeline.clientHeight);
    autoFollowRef.current = follow;
    // 滚动事件很密集：状态没变就不要触发重渲染。
    setShowJumpToBottom((current) => (current === !follow ? current : !follow));
  }

  function jumpToBottom() {
    autoFollowRef.current = true;
    setShowJumpToBottom(false);
    timelineRef.current?.scrollTo({ top: timelineRef.current.scrollHeight, behavior: "smooth" });
  }

  function beginPlanResize(event: ReactPointerEvent<HTMLDivElement>) {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = planWidth;
    const move = (moveEvent: PointerEvent) =>
      setPlanWidth(Math.min(680, Math.max(260, startWidth + startX - moveEvent.clientX)));
    const stop = () => {
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerup", stop);
    };
    document.addEventListener("pointermove", move);
    document.addEventListener("pointerup", stop);
  }

  async function activate(projectId: string) {
    if (busy) {
      setError("当前请求仍在处理，请先等待完成或停止后再切换项目。");
      return;
    }
    try {
      setError("");
      setTranscript([]);
      setLocalTurns([]);
      setShowProjectPicker(false);
      await projectsFeature.activate(projectId);
    } catch (reason) {
      setError((reason as Error).message);
    }
  }

  async function trust(event: FormEvent) {
    event.preventDefault();
    setTranscript([]);
    setLocalTurns([]);
    setShowProjectPicker(false);
    try {
      await projectsFeature.trust(projectsFeature.trustPath);
    } catch (reason) {
      setError((reason as Error).message);
    }
  }

  async function send(event: FormEvent) {
    event.preventDefault();
    if (!message.trim() || busy) return;
    const text = message.trim();
    const local = newLocalTurn(text);
    try {
      setBusy(true);
      setError("");
      autoFollowRef.current = true;
      setShowJumpToBottom(false);
      setLocalTurns((items) => [...items, local]);
      setMessage("");
      await api("/turns", { method: "POST", body: JSON.stringify({ text }) });
    } catch (reason) {
      setBusy(false);
      const detail = (reason as Error).message;
      setLocalTurns((items) => failUnboundTurn(items, local.clientId, detail));
      setError(detail);
    }
  }

  async function cancelTurn() {
    try {
      setStopping(true);
      await api("/turns/current/cancel", { method: "POST" });
    } catch (reason) {
      setStopping(false);
      setError((reason as Error).message);
    }
  }

  async function createSession() {
    try {
      const session = await api<Session>("/sessions", { method: "POST" });
      setCurrentSessionId(session.session_id);
      sessionScopeRef.current = session.session_id;
      setTranscript([]);
      setLocalTurns([]);
      // 还没渲染的那一批属于上一个会话, 排空它们 —— 否则 setLocalTurns([]) 之后
      // 它们会自己长回来 (ADR-0048 决策 2)。
      discardPending();
      await loadTranscript(session.session_id);
      await loadWorkspace();
    } catch (reason) {
      setError((reason as Error).message);
    }
  }

  async function resumeSession(sessionId: string) {
    try {
      await api(`/sessions/${sessionId}/resume`, { method: "POST" });
      setCurrentSessionId(sessionId);
      sessionScopeRef.current = sessionId;
      setLocalTurns([]);
      discardPending();
      await loadTranscript(sessionId);
    } catch (reason) {
      setError((reason as Error).message);
    }
  }

  /** 删掉一个会话及其计划、处理过程与恢复点。不可撤销, 所以入口是两步确认。 */
  async function deleteSession(sessionId: string) {
    try {
      setDeletingSession(true);
      // 后端只保证"已经从列表消失"就返回, 文件在它那边后台清 —— 所以这里拿不到
      // 也不该等一个"清了几个恢复点"的数字。
      const result = await api<{ current_session_id: string; cleanup: string }>(`/sessions/${sessionId}`, {
        method: "DELETE",
      });
      setConfirmDelete("");
      // 删的是当前会话时后端已经开了一个新的, 切过去 —— 不自己猜停在哪。
      if (result.current_session_id !== currentSessionId) {
        setCurrentSessionId(result.current_session_id);
        sessionScopeRef.current = result.current_session_id;
        setTranscript([]);
        setLocalTurns([]);
        discardPending();
        await loadTranscript(result.current_session_id);
      }
      await loadWorkspace();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setDeletingSession(false);
    }
  }

  /** 只送要改的那一个轴; 另一个由后端保持不变。 */
  async function changeStance(patch: Partial<Stance>) {
    try {
      await api("/mode", { method: "POST", body: JSON.stringify(patch) });
      setStance((current) => ({ ...current, ...patch }));
    } catch (reason) {
      setError((reason as Error).message);
    }
  }

  async function openSettings() {
    setShowSettings(true);
    try {
      await admin.loadAll();
    } catch (reason) {
      setError((reason as Error).message);
    }
  }

  if (!activeProjectId) {
    return (
      <main className="project-center">
        <div className="brand-lockup">
          <span className="forge-mark">F</span>
          <div>
            <h1>Forge</h1>
            <p>本地优先的 AI 工程工作台</p>
          </div>
        </div>
        {error && <div className="error-banner">{error}</div>}
        <section className="project-grid">
          {projectsFeature.projects.map((project) => (
            <button
              className="project-card"
              key={project.project_id}
              onClick={() => activate(project.project_id)}
            >
              <span className="project-icon">⌘</span>
              <strong>{shortPath(project.primary_workspace_root)}</strong>
              <small>{project.primary_workspace_root}</small>
              <span>打开项目 →</span>
            </button>
          ))}
          <form className="project-card add-project" onSubmit={trust}>
            <strong>信任新项目</strong>
            <small>输入本机目录的绝对路径</small>
            <input
              value={projectsFeature.trustPath}
              onChange={(event) => projectsFeature.setTrustPath(event.target.value)}
              placeholder="/path/to/repository"
            />
            <button type="submit">添加并打开</button>
          </form>
        </section>
      </main>
    );
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="top-brand">
          <span className="forge-mark small">F</span>
          <strong>Forge</strong>
        </div>
        <button
          className="project-picker"
          onClick={() => setShowProjectPicker((value) => !value)}
          aria-expanded={showProjectPicker}
          title={projectsFeature.activeProject?.primary_workspace_root}
        >
          <span>
            {projectsFeature.activeProject
              ? shortPath(projectsFeature.activeProject.primary_workspace_root)
              : "选择项目"}
          </span>
          <ChevronIcon className="picker-caret" />
        </button>
        <span className={`connection ${connection}`} title={connectionCopy[connection].hint}>
          <i />
          <span className="connection-label">{connectionCopy[connection].label}</span>
          {retryDelay > 0 && <em>{formatDelay(retryDelay)}</em>}
        </span>
        <button
          className={`plan-toggle ${showPlan ? "active" : ""}`}
          onClick={() => setShowPlan((value) => !value)}
          aria-pressed={showPlan}
          aria-label={showPlan ? "隐藏计划" : "显示计划"}
        >
          <PlanIcon />
          {planningFeature.planning.plan?.status === "proposed" && (
            <i className="plan-badge" title="有计划待评审" />
          )}
        </button>
        <button className="icon-button" onClick={openSettings} aria-label="设置">
          <GearIcon />
        </button>
      </header>
      {showProjectPicker && (
        <ProjectPicker
          projects={projectsFeature.projects}
          activeProjectId={activeProjectId}
          busy={busy}
          trustPath={projectsFeature.trustPath}
          onTrustPath={projectsFeature.setTrustPath}
          onTrust={trust}
          onActivate={activate}
          onCancel={() => setShowProjectPicker(false)}
        />
      )}
      {requests.busy && <div className="request-bar" role="progressbar" aria-label="正在请求" />}
      {requests.slow.length > 0 && (
        <div className="slow-banner" role="status">
          接口响应已超过 {Math.round(SLOW_REQUEST_MS / 1000)} 秒，仍在等待：{requests.slow.join("、")}
        </div>
      )}
      {error && (
        <div className="error-banner workspace-error" role="alert">
          <span>{error}</span>
          <button onClick={() => setError("")} aria-label="关闭提示">
            ×
          </button>
        </div>
      )}
      <div
        className={`workspace-grid ${showPlan ? "with-plan" : ""}`}
        style={{ "--context-width": `${planWidth}px` } as CSSProperties}
      >
        <aside className="sidebar">
          <div className="sidebar-title">
            <span>会话</span>
            <button onClick={createSession}>＋</button>
          </div>
          <nav className="session-list">
            {sessions.map((session) => (
              <div
                className={`session-row ${session.session_id === currentSessionId ? "active" : ""}`}
                key={session.session_id}
              >
                <button
                  className="session-open"
                  onClick={() => resumeSession(session.session_id)}
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
                  onClick={() => setConfirmDelete(session.session_id)}
                >
                  ×
                </button>
              </div>
            ))}
            {!sessions.length && <p className="empty-copy">发送第一条消息后，会话会出现在这里。</p>}
          </nav>
          <div className="workspace-roots">
            <span>工作区</span>
            {projectsFeature.activeProject?.workspace_roots.map((root) => (
              <small key={root} title={root}>
                ● {root}
              </small>
            ))}
          </div>
        </aside>

        <section className="conversation">
          <div className="timeline" ref={timelineRef} onScroll={handleTimelineScroll}>
            {!transcript.length && !localTurns.length && (
              <div className="welcome">
                <div className="forge-mark">F</div>
                <h2>准备好了</h2>
                <p>描述你想理解、规划或修改的工程任务。</p>
              </div>
            )}
            {transcript.map((item) => {
              const run =
                item.payload.role === "assistant"
                  ? restoredByTurn.get(item.payload.turn_id ?? "")
                  : undefined;
              // 有过程就渲染过程 —— 最终回答是它最后一块叙述, 再画一遍 Message 就重复了。
              // 没有过程的旧会话 (runs.jsonl 之前的) 仍然只渲染回答, 那是它们全部的内容。
              if (run)
                return (
                  <RestoredTurnView
                    run={run}
                    item={item}
                    directory={admin.toolDirectory}
                    key={item.event_id}
                  />
                );
              return <Message item={item} key={item.event_id} />;
            })}
            {localTurns.map((turn) => (
              <LocalTurnView turn={turn} directory={admin.toolDirectory} key={turn.clientId} />
            ))}
          </div>
          <div className="conversation-footer">
            {showJumpToBottom && (
              <button className="jump-bottom" onClick={jumpToBottom}>
                回到底部 ↓
              </button>
            )}
            {promptsFeature.prompts[0] && (
              <PromptCard
                // 草稿按 (会话, 提示) 绑定 (ADR-0048 决策 5): 重连时同一条提示的卡片不重建,
                // 已经写下的字留着; 换会话或这条提示结束时卡片卸载, 草稿跟着走。
                key={`${currentSessionId}:${promptsFeature.prompts[0].prompt_id}`}
                prompt={promptsFeature.prompts[0]}
                onResolve={promptsFeature.resolve}
              />
            )}
            <form className={`composer ${connection === "stopped" ? "offline" : ""}`} onSubmit={send}>
              <textarea
                ref={composerRef}
                value={message}
                rows={1}
                disabled={connection === "stopped"}
                onChange={(event) => setMessage(event.target.value)}
                onCompositionStart={() => {
                  composingRef.current = true;
                }}
                onCompositionEnd={() => {
                  composingRef.current = false;
                }}
                onKeyDown={(event) => {
                  if (
                    shouldSendOnEnter(
                      event.key,
                      event.shiftKey,
                      event.nativeEvent.isComposing,
                      composingRef.current,
                    )
                  ) {
                    event.preventDefault();
                    event.currentTarget.form?.requestSubmit();
                  }
                }}
                placeholder={
                  connection === "stopped"
                    ? "连不上本地服务，请重新运行 forge 后刷新页面"
                    : "描述你想理解、规划或修改的工程任务…"
                }
              />
              <div className="composer-bar">
                <ModeMenu value={stance} disabled={busy} onChange={changeStance} />
                <ModelMenu
                  current={admin.currentModel}
                  models={configuredModels}
                  thinking={admin.thinking}
                  disabled={busy}
                  onChoose={admin.chooseCurrentModel}
                  onThinking={admin.updateThinking}
                />
                <span className="composer-hint">Enter 发送 · Shift+Enter 换行</span>
                {busy ? (
                  <button type="button" className="stop" onClick={cancelTurn} disabled={stopping}>
                    {stopping ? "停止中…" : "停止"}
                  </button>
                ) : (
                  <button type="submit" disabled={!message.trim() || connection === "stopped"}>
                    发送 ↑
                  </button>
                )}
              </div>
            </form>
          </div>
        </section>

        {showPlan && (
          <>
            <div className="context-resizer" onPointerDown={beginPlanResize} title="拖动调整计划栏宽度" />
            <aside className="context-panel">
              <div className="panel-heading">
                <strong>计划</strong>
                <div>
                  <button
                    onClick={() => window.open("/api/v1/planning/markdown", "_blank", "noopener,noreferrer")}
                    disabled={!planningFeature.planning.plan}
                  >
                    打开 Markdown
                  </button>
                  <button onClick={() => setShowPlan(false)} aria-label="隐藏计划">
                    ×
                  </button>
                </div>
              </div>
              <PlanningPanel
                planning={planningFeature.planning}
                index={planningFeature.index}
                onResolve={planningFeature.resolveReview}
                onActivate={planningFeature.activatePlan}
              />
            </aside>
          </>
        )}
      </div>

      {confirmDelete && (
        <ConfirmDialog
          title="删除这个会话？"
          body={`「${sessions.find((item) => item.session_id === confirmDelete)?.title || "未命名会话"}」的计划、待办、处理过程与恢复点会一起删掉，这个会话做过的改动将无法撤销。工作区里的文件不受影响。`}
          confirmLabel="删除"
          busy={deletingSession}
          onConfirm={() => deleteSession(confirmDelete)}
          onCancel={() => setConfirmDelete("")}
        />
      )}

      {showSettings && (
        <SettingsPanel
          items={settingsFeature.settings}
          roots={admin.workspaceRoots}
          rules={admin.rules}
          checkpoints={admin.checkpoints}
          providerSettings={admin.providerSettings}
          providerFields={admin.providerFields}
          modelFields={admin.modelFields}
          providers={admin.providers}
          knownProviders={admin.knownProviders}
          llmRuntime={admin.llmRuntime}
          catalog={{
            currentModel: admin.currentModel,
            overrides: admin.overrides,
            origins: admin.origins,
            thinking: admin.thinking,
            tools: admin.tools,
            status: admin.statusView,
            recovery: admin.recovery,
          }}
          actions={{
            onSetCurrentModel: admin.chooseCurrentModel,
            onSetOverride: admin.setModelOverride,
            onClearOverride: admin.clearModelOverride,
            onPruneRules: admin.pruneRules,
            onUndo: admin.undoLatest,
            onPreviewCheckpoint: admin.previewCheckpoint,
          }}
          onClose={() => setShowSettings(false)}
          onSave={settingsFeature.save}
          onReset={settingsFeature.reset}
          onAddRoot={admin.addWorkspace}
          onRemoveRoot={admin.removeWorkspace}
          onRevokeRule={admin.revokeRule}
          onRestore={admin.restoreCheckpoint}
          onAddModel={admin.addModel}
          onRemoveModel={admin.removeModel}
          onSaveModel={admin.saveModelFields}
          providerProtocols={admin.providerProtocols}
          onAddProvider={admin.addProvider}
          onSaveProvider={admin.saveProviderFields}
          onSaveLlmRuntime={admin.saveLlmRuntime}
        />
      )}
    </main>
  );
}

export default App;
