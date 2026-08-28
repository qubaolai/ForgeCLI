import { CSSProperties, FormEvent, Fragment, PointerEvent as ReactPointerEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CheckIcon, ChevronIcon, GearIcon, PlanIcon, ShieldIcon } from "./icons";
import { CopyButton, Markdown } from "./Markdown";
import { RunProcess } from "./RunProcess";
import { buildInitialModelParams, modelPlaceholder, modelRef, providerAvailabilityCopy, splitModelRef } from "./modelSettings";
import { appendRunEvent, failUnboundTurn, finishLocalTurn, formatTokens, isTerminalEvent, LocalTurn, metricsFor, newLocalTurn, restoreTurn, RunEvent, RunSnapshot, shouldAutoFollow, shouldSendOnEnter } from "./runModel";

type Project = {
  project_id: string;
  primary_workspace_root: string;
  workspace_roots: string[];
};

type Session = {
  session_id: string;
  title: string;
  updated_at: string;
  status: string;
  mode?: string;
};

type TranscriptEvent = {
  event_id: string;
  type: string;
  created_at: string;
  payload: { role?: string; text?: string; status?: string; turn_id?: string };
};

type Setting = {
  key: string;
  // 中文名与说明来自后端 SCHEMA，不在这里另写一份：同一个开关在终端菜单和这里叫不同
  // 名字时，两边都不会报错，只会让用户以为是两个开关。
  label: string;
  help: string;
  level: string;
  kind: string;
  value: string;
  choices: string[];
};

type Approval = {
  approval_id: string;
  mandatory: boolean;
  view: {
    mode: string;
    workspace_roots: string[];
    raw_command: string;
    target_groups: Array<{ label: string; paths: string[] }>;
    script_snapshots: Array<{ path?: string; content?: string }>;
    allowed_scopes: string[];
    unresolved_reason?: string;
  };
};

type Planning = {
  markdown?: string;
  plan?: {
    plan_id: string;
    title: string;
    goal: string;
    status: string;
    steps: Array<{ title: string; detail: string }>;
  };
  todo?: {
    items: Array<{ title: string; status: string }>;
  };
};

type TurnRunState = {
  run_id: string;
  status: string;
  response?: { turn_id: string; text: string };
  error?: string;
};

type WorkspaceRoot = { path: string; access: string };
type LearnedRule = {
  rule_id: string;
  label: string;
  scope: string;
  revoked: boolean;
  match: { mode: string };
};
type Checkpoint = {
  checkpoint_id: string;
  status: string;
  snapshot_strategy: string;
  created_at: string;
  mutations: { entries?: unknown[] };
};
type Model = {
  provider: string;
  id: string;
  params: Record<string, unknown> & {
    context_window?: number;
    max_tokens?: number;
    temperature?: number;
    top_p?: number;
    thinking_mode?: string;
    thinking_effort?: string;
    thinking_efforts?: string[];
    thinking_default_effort?: string;
  };
};
type Provider = {
  id: string;
  name: string;
  api_base: string;
  api_key_env?: string;
  timeout: number;
  max_retries: number;
  models: Model[];
};
type KnownProvider = { id: string; label: string; api_key_env?: string; available: boolean };
type ThinkingView = {
  model: string;
  configured: boolean;
  mode?: string;
  effort?: string;
  supported_efforts?: string[];
};
type ToolSpec = {
  name: string;
  title: string;
  description: string;
  declared_capabilities: string[];
  default_timeout_seconds: number;
};
type StatusView = {
  session_id: string;
  mode: string;
  last_event_id: string;
  workspace_roots: string[];
  model: string;
  busy: boolean;
};
type RecoveryStatus = {
  checkpoint_count: number;
  pending: Array<{ checkpoint_id: string; status: string; created_at: string }>;
};
type PlanIndexView = {
  active_plan_id: string;
  active_todo_id: string;
  plans: Array<{ plan_id: string; revision: number; status: string; title: string; updated_at: string }>;
};
/** 设置面板要展示的只读快照, 与它能触发的动作分开传, 免得再堆十几个平铺 prop。 */
type AdminCatalog = {
  currentModel: string;
  overrides: Record<string, string>;
  origins: string[];
  thinking: ThinkingView;
  tools: ToolSpec[];
  status: StatusView | null;
  recovery: RecoveryStatus | null;
};
type AdminActions = {
  onSetCurrentModel: (providerId: string, modelId: string) => Promise<boolean>;
  onSetOverride: (origin: string, providerId: string, modelId: string) => void;
  onClearOverride: (origin: string) => void;
  onUpdateThinking: (mode: string, effort: string) => void;
  onPruneRules: () => void;
  onUndo: () => void;
  onPreviewCheckpoint: (id: string) => Promise<string>;
};
type FieldSpec = { name: string; label: string; kind: string };
type ModelsResponse = {
  items: Provider[];
  // 每家内置供应商各一条（没配过的带注册表默认值）：要能在添加第一个模型之前就把端点填好。
  provider_settings: Provider[];
  // 表单该有哪些行、每行叫什么——从后端的 ProviderConfig 字段声明派生。
  provider_fields: FieldSpec[];
  model_fields: FieldSpec[];
  known_providers: KnownProvider[];
  runtime: LlmRuntimeSettings;
  current_model: string;
  overrides: Record<string, string>;
  origins: string[];
  thinking: ThinkingView;
};
type LlmRuntimeSettings = {
  cache: { enabled: boolean; ttl_seconds?: number; max_entries: number; origins: string[] };
  circuit_breaker: { enabled: boolean; failure_threshold: number; cooldown_seconds: number };
  retry: { wait_threshold_seconds: number };
};

type ConnectionState = "connecting" | "live" | "retrying" | "stopped";

const connectionCopy: Record<ConnectionState, { label: string; hint: string }> = {
  connecting: { label: "连接中", hint: "正在建立本地事件流" },
  live: { label: "本地已连接", hint: "事件流正常" },
  retrying: { label: "重连中", hint: "本地服务不可达，正在按退避节奏自动重连" },
  stopped: { label: "已停止重试", hint: "长时间连不上本地服务；重新运行 forge 后刷新页面即可" },
};

// 一帧合并一次事件；33ms 对流式正文足够顺滑，又不会让每条增量都触发一次整树重渲染。
const flushIntervalMs = 33;

// 重连间隔从 1 秒开始逐次翻倍；一旦下一次要等超过一小时，就不再自动重试。
const reconnectBaseMs = 1000;
const reconnectFactor = 2;
const reconnectCeilingMs = 60 * 60 * 1000;

const modeOptions = [
  { value: "plan", label: "Plan", hint: "只出方案，先评审再动手" },
  { value: "accept_edits", label: "Accept Edits", hint: "自动接受文件编辑" },
  { value: "auto", label: "Auto", hint: "自动执行，风险操作仍需审批" },
  { value: "full_access", label: "Full Access", hint: "打断最少，权限最大" },
];

function formatDelay(milliseconds: number) {
  const seconds = Math.round(milliseconds / 1000);
  if (seconds < 60) return `${seconds} 秒后重试`;
  return `${Math.round(seconds / 60)} 分钟后重试`;
}

let csrfToken = "";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body) headers.set("Content-Type", "application/json");
  if (init?.method && init.method !== "GET") headers.set("X-CSRF-Token", csrfToken);
  const response = await fetch(`/api/v1${path}`, { ...init, headers });
  if (!response.ok) {
    // 服务重启后旧 cookie 不再有效，直接说清楚怎么恢复，而不是抛一个裸 401。
    if (response.status === 401) throw new Error("本地会话已失效，请回到终端重新打开 Forge 启动链接。");
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json() as { detail?: string };
      detail = body.detail ?? detail;
    } catch { /* use HTTP status */ }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

function shortPath(path: string) {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.at(-1) || path;
}

/** 遮罩层统一的 Esc 关闭；避免每个面板各写一份 window 监听。 */
function useEscape(active: boolean, onEscape: () => void) {
  useEffect(() => {
    if (!active) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") onEscape();
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [active, onEscape]);
}

function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<TranscriptEvent[]>([]);
  const [restoredRuns, setRestoredRuns] = useState<LocalTurn[]>([]);
  const [localTurns, setLocalTurns] = useState<LocalTurn[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState("accept_edits");
  const [error, setError] = useState("");
  const [trustPath, setTrustPath] = useState("");
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [planning, setPlanning] = useState<Planning>({});
  const [settings, setSettings] = useState<Setting[]>([]);
  const [showSettings, setShowSettings] = useState(false);
  const [workspaceRoots, setWorkspaceRoots] = useState<WorkspaceRoot[]>([]);
  const [rules, setRules] = useState<LearnedRule[]>([]);
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [knownProviders, setKnownProviders] = useState<KnownProvider[]>([]);
  const [providerSettings, setProviderSettings] = useState<Provider[]>([]);
  const [providerFields, setProviderFields] = useState<FieldSpec[]>([]);
  const [modelFields, setModelFields] = useState<FieldSpec[]>([]);
  const [llmRuntime, setLlmRuntime] = useState<LlmRuntimeSettings | null>(null);
  const [currentModel, setCurrentModel] = useState("");
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [origins, setOrigins] = useState<string[]>([]);
  const [thinking, setThinking] = useState<ThinkingView>({ model: "", configured: false });
  const [tools, setTools] = useState<ToolSpec[]>([]);
  const [statusView, setStatusView] = useState<StatusView | null>(null);
  const [recovery, setRecovery] = useState<RecoveryStatus | null>(null);
  const [planIndex, setPlanIndex] = useState<PlanIndexView | null>(null);
  const [showPlan, setShowPlan] = useState(false);
  const [planWidth, setPlanWidth] = useState(320);
  const [showProjectPicker, setShowProjectPicker] = useState(false);
  const [showJumpToBottom, setShowJumpToBottom] = useState(false);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [retryDelay, setRetryDelay] = useState(0);
  const [stopping, setStopping] = useState(false);
  const timelineRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const autoFollowRef = useRef(true);
  const composingRef = useRef(false);
  const announcedPlanRef = useRef("");
  const lastEventIdRef = useRef("");
  const pendingEventsRef = useRef<RunEvent[]>([]);
  const flushHandleRef = useRef(0);
  const refreshHandleRef = useRef(0);

  const activeProject = useMemo(
    () => projects.find((item) => item.project_id === activeProjectId),
    [projects, activeProjectId],
  );

  const configuredModels = useMemo(
    () => providers.flatMap((provider) => provider.models.map((model) => `${provider.id}:${model.id}`)),
    [providers],
  );

  const restoredByTurn = useMemo(() => {
    const live = new Set(localTurns.map((turn) => turn.turnId));
    return new Map(restoredRuns
      .filter((turn) => turn.turnId && !live.has(turn.turnId))
      .map((turn) => [turn.turnId as string, turn]));
  }, [restoredRuns, localTurns]);

  const loadProjects = useCallback(async () => {
    const result = await api<{ items: Project[]; active_project_id: string | null }>("/projects");
    setProjects(result.items);
    setActiveProjectId(result.active_project_id);
  }, []);

  const loadWorkspace = useCallback(async () => {
    const [sessionResult, approvalResult, planningResult, settingResult, runResult] = await Promise.all([
      api<{ current_session_id: string; current_session: Session; items: Session[] }>("/sessions"),
      api<{ items: Approval[] }>("/approvals"),
      api<Planning>("/planning"),
      api<{ items: Setting[] }>("/settings"),
      api<TurnRunState | null>("/turns/current"),
    ]);
    setSessions(sessionResult.items);
    setCurrentSessionId(sessionResult.current_session_id);
    setMode(sessionResult.current_session.mode ?? "accept_edits");
    setApprovals(approvalResult.items);
    setPlanning(planningResult);
    api<PlanIndexView>("/plans").then(setPlanIndex).catch(() => undefined);
    setSettings(settingResult.items);
    // 服务端才是"是否还在跑"的真相源：刷新页面后按它恢复运行态。
    setBusy(runResult?.status === "running");
  }, []);

  const loadTranscript = useCallback(async (sessionId: string) => {
    try {
      const result = await api<{ items: TranscriptEvent[] }>(`/sessions/${sessionId}/transcript`);
      setTranscript(result.items);
    } catch {
      setTranscript([]);
    }
    // 处理过程只活在进程内：服务重启后拿不到，但刷新页面仍然能展开看。
    try {
      const runs = await api<{ items: RunSnapshot[] }>("/runs");
      setRestoredRuns(runs.items.map((item) => restoreTurn(item)));
    } catch {
      setRestoredRuns([]);
    }
  }, []);

  const loadModels = useCallback(async () => {
    const models = await api<ModelsResponse>("/models");
    setProviders(models.items);
    setProviderSettings(models.provider_settings);
    setProviderFields(models.provider_fields);
    setModelFields(models.model_fields);
    setKnownProviders(models.known_providers);
    setLlmRuntime(models.runtime);
    setCurrentModel(models.current_model);
    setOverrides(models.overrides);
    setOrigins(models.origins);
    setThinking(models.thinking);
  }, []);

  const loadAdministration = useCallback(async () => {
    const [roots, ruleList, checkpointList, toolList, status, recoveryState] = await Promise.all([
      api<{ items: WorkspaceRoot[] }>("/workspace-roots"),
      api<{ items: LearnedRule[] }>("/rules"),
      api<{ items: Checkpoint[] }>("/checkpoints"),
      api<{ items: ToolSpec[] }>("/tools"),
      api<StatusView>("/status"),
      api<RecoveryStatus>("/recovery"),
    ]);
    setWorkspaceRoots(roots.items);
    setRules(ruleList.items);
    setCheckpoints(checkpointList.items);
    setTools(toolList.items);
    setStatusView(status);
    setRecovery(recoveryState);
    await loadModels();
  }, [loadModels]);

  useEffect(() => {
    api<{ csrf_token: string; active_project_id: string | null }>("/bootstrap")
      .then((value) => { csrfToken = value.csrf_token; })
      .then(loadProjects)
      .catch((reason: Error) => setError(reason.message));
  }, [loadProjects]);

  useEffect(() => {
    if (!activeProjectId) return;
    loadWorkspace().catch((reason: Error) => setError(reason.message));
    loadModels().catch(() => undefined);
  }, [activeProjectId, loadWorkspace, loadModels]);

  useEffect(() => {
    if (currentSessionId) loadTranscript(currentSessionId);
  }, [currentSessionId, loadTranscript]);

  useEffect(() => {
    const theme = settings.find((item) => item.key === "output.theme")?.value;
    if (theme === "dark" || theme === "light") {
      document.documentElement.dataset.theme = theme;
    }
  }, [settings]);

  const flushEvents = useCallback(() => {
    flushHandleRef.current = 0;
    const batch = pendingEventsRef.current;
    if (!batch.length) return;
    pendingEventsRef.current = [];
    // 一帧合并一次：事件洪峰（重连补发、密集流式增量）不该变成几百次 render。
    setLocalTurns((items) => batch.reduce((carry, event) => appendRunEvent(carry, event), items));
  }, []);


  const queueEvent = useCallback((event: RunEvent) => {
    pendingEventsRef.current.push(event);
    // 用定时器而不是 requestAnimationFrame: 标签页切走时 rAF 完全不触发, 事件会一直
    // 堆在缓冲里不渲染也不释放。定时器在后台只是被降频, 缓冲照样能排空。
    if (!flushHandleRef.current) {
      flushHandleRef.current = window.setTimeout(flushEvents, flushIntervalMs);
    }
  }, [flushEvents]);

  const flushNow = useCallback(() => {
    if (flushHandleRef.current) window.clearTimeout(flushHandleRef.current);
    flushEvents();
  }, [flushEvents]);

  const refreshWorkspace = useCallback(() => {
    // 工作区刷新是 5 个并行请求；密集事件下不节流会把浏览器的连接数吃光。
    if (refreshHandleRef.current) return;
    refreshHandleRef.current = window.setTimeout(() => {
      refreshHandleRef.current = 0;
      loadWorkspace().catch(() => undefined);
    }, 300);
  }, [loadWorkspace]);

  useEffect(() => () => {
    if (flushHandleRef.current) window.clearTimeout(flushHandleRef.current);
    if (refreshHandleRef.current) window.clearTimeout(refreshHandleRef.current);
  }, []);

  useEscape(showProjectPicker, useCallback(() => setShowProjectPicker(false), []));
  useEscape(showSettings, useCallback(() => setShowSettings(false), []));

  useEffect(() => {
    if (!activeProjectId) return;
    let source: EventSource | null = null;
    let timer = 0;
    let delay = reconnectBaseMs;
    let disposed = false;
    let opened = false;

    // 事件类型放在 data.kind 里，页面只订阅一个名字，后端新增事件不会被静默丢弃。
    const consume = (raw: MessageEvent<string>) => {
      if (raw.lastEventId) lastEventIdRef.current = raw.lastEventId;
      const event = JSON.parse(raw.data) as RunEvent;
      queueEvent(event);
      if (isTerminalEvent(event)) {
        flushNow();
        setBusy(false);
        refreshWorkspace();
        window.setTimeout(() => syncFinishedTurn(event.turn_id), 60);
      }
      if (["approval_requested", "approval_resolved", "plan_proposed", "todo_updated"].includes(event.kind)) {
        refreshWorkspace();
      }
    };

    const scheduleRetry = () => {
      if (disposed) return;
      if (delay > reconnectCeilingMs) {
        // 间隔已经超过一小时，继续自动重试没有意义；刷新页面即可重新开始。
        setRetryDelay(0);
        setConnection("stopped");
        return;
      }
      const wait = delay;
      delay *= reconnectFactor;
      setRetryDelay(wait);
      setConnection("retrying");
      timer = window.setTimeout(connect, wait);
    };

    function connect() {
      if (disposed) return;
      const resume = lastEventIdRef.current;
      source = new EventSource(`/api/v1/events${resume ? `?after=${encodeURIComponent(resume)}` : ""}`);
      source.addEventListener("run_event", consume as EventListener);
      source.addEventListener("resync_required", (raw) => {
        lastEventIdRef.current = (raw as MessageEvent).lastEventId || lastEventIdRef.current;
        refreshWorkspace();
      });
      source.addEventListener("server_stopping", () => {
        // 服务在正常退出。端口与会话密钥都是固定的，等它起来就能自己连回去。
        source?.close();
        source = null;
        delay = reconnectBaseMs;
        scheduleRetry();
      });
      source.onopen = () => {
        delay = reconnectBaseMs;
        setRetryDelay(0);
        setConnection("live");
        if (opened) {
          // 断线期间可能换了进程：项目、会话和运行态都要重新对齐。
          loadProjects().catch(() => undefined);
          refreshWorkspace();
        }
        opened = true;
      };
      source.onerror = () => {
        // 接管 EventSource 自带的固定间隔重连，才能做退避并在超过上限后停下来。
        source?.close();
        source = null;
        if (disposed) return;
        // 会话密钥跨重启复用，正常不会走到这里；真失效时给出可操作提示。
        fetch("/api/v1/bootstrap").then(
          (response) => (response.status === 401 ? "stale" : "retry"),
          () => "retry",
        ).then((verdict) => {
          if (disposed) return;
          if (verdict === "stale") setError("本地会话已失效，请回到终端重新打开 Forge 启动链接。");
          scheduleRetry();
        });
      };
    }

    setConnection("connecting");
    connect();
    return () => {
      disposed = true;
      window.clearTimeout(timer);
      source?.close();
    };
  }, [activeProjectId, loadProjects, refreshWorkspace, queueEvent, flushNow]);

  useEffect(() => {
    if (!busy) setStopping(false);
  }, [busy]);

  useEffect(() => {
    // 计划待评审时决议按钮只在计划栏里，所以新提案必须自己弹出来一次。
    const plan = planning.plan;
    if (!plan || plan.status !== "proposed" || announcedPlanRef.current === plan.plan_id) return;
    announcedPlanRef.current = plan.plan_id;
    setShowPlan(true);
  }, [planning.plan]);

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
    } catch (reason) {
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
    const move = (moveEvent: PointerEvent) => setPlanWidth(Math.min(680, Math.max(260, startWidth + startX - moveEvent.clientX)));
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
      await api(`/projects/${projectId}/activate`, { method: "POST" });
      setTranscript([]);
      setLocalTurns([]);
      setShowProjectPicker(false);
      await loadProjects();
    } catch (reason) { setError((reason as Error).message); }
  }

  async function trust(event: FormEvent) {
    event.preventDefault();
    try {
      const project = await api<Project>("/projects", {
        method: "POST", body: JSON.stringify({ path: trustPath }),
      });
      setTrustPath("");
      await loadProjects();
      await activate(project.project_id);
    } catch (reason) { setError((reason as Error).message); }
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
      setTranscript([]);
      setLocalTurns([]);
      await loadWorkspace();
    } catch (reason) { setError((reason as Error).message); }
  }

  async function resumeSession(sessionId: string) {
    try {
      await api(`/sessions/${sessionId}/resume`, { method: "POST" });
      setCurrentSessionId(sessionId);
      setLocalTurns([]);
      await loadTranscript(sessionId);
    } catch (reason) { setError((reason as Error).message); }
  }

  async function changeMode(value: string) {
    try {
      await api("/mode", { method: "POST", body: JSON.stringify({ mode: value }) });
      setMode(value);
    } catch (reason) { setError((reason as Error).message); }
  }

  async function resolveApproval(id: string, decision: string) {
    try {
      await api(`/approvals/${id}/resolve`, {
        method: "POST", body: JSON.stringify({ decision }),
      });
      await loadWorkspace();
    } catch (reason) { setError((reason as Error).message); }
  }

  async function resolvePlan(decision: string) {
    try {
      await api("/plan-reviews/current/resolve", {
        method: "POST", body: JSON.stringify({ decision, note: "" }),
      });
      await loadWorkspace();
    } catch (reason) { setError((reason as Error).message); }
  }

  async function saveSetting(item: Setting, value: string) {
    try {
      await api(`/settings/${item.key}`, {
        method: "PATCH", body: JSON.stringify({ value }),
      });
      await loadWorkspace();
    } catch (reason) { setError((reason as Error).message); }
  }

  async function openSettings() {
    setShowSettings(true);
    try { await loadAdministration(); } catch (reason) { setError((reason as Error).message); }
  }

  async function addWorkspace(path: string, access: string) {
    try {
      await api("/workspace-roots", { method: "POST", body: JSON.stringify({ path, access }) });
      await Promise.all([loadProjects(), loadAdministration()]);
    } catch (reason) { setError((reason as Error).message); }
  }

  async function removeWorkspace(path: string) {
    try {
      await api(`/workspace-roots?path=${encodeURIComponent(path)}`, { method: "DELETE" });
      await Promise.all([loadProjects(), loadAdministration()]);
    } catch (reason) { setError((reason as Error).message); }
  }

  async function revokeRule(id: string) {
    try {
      await api(`/rules/${id}`, { method: "DELETE" });
      await loadAdministration();
    } catch (reason) { setError((reason as Error).message); }
  }

  async function restoreCheckpoint(id: string) {
    try {
      await api(`/checkpoints/${id}/restore`, { method: "POST", body: JSON.stringify({ force_conflicts: false }) });
      await loadAdministration();
    } catch (reason) { setError((reason as Error).message); }
  }

  async function addModel(providerId: string, modelId: string, params: Record<string, unknown>) {
    try {
      await api("/models", {
        method: "POST",
        body: JSON.stringify({ provider_id: providerId, model_id: modelId, params }),
      });
      await loadModels();
      return true;
    } catch (reason) {
      setError((reason as Error).message);
      return false;
    }
  }

  async function removeModel(providerId: string, modelId: string) {
    try {
      await api(`/models?provider_id=${encodeURIComponent(providerId)}&model_id=${encodeURIComponent(modelId)}`, { method: "DELETE" });
      await loadModels();
    } catch (reason) { setError((reason as Error).message); }
  }

  /** 逐字段 PATCH, 但只在整批做完后刷新一次: 中途刷新会把还没保存的输入冲掉。 */
  async function saveFields(changed: Record<string, string>, endpoint: (field: string) => string) {
    try {
      for (const [field, value] of Object.entries(changed)) {
        await api(endpoint(field), { method: "PATCH", body: JSON.stringify({ value }) });
      }
    } catch (reason) {
      setError((reason as Error).message);
    }
    await loadModels().catch(() => undefined);
  }

  function saveModelFields(providerId: string, modelId: string, changed: Record<string, string>) {
    return saveFields(changed, (field) =>
      `/models/${field}?provider_id=${encodeURIComponent(providerId)}&model_id=${encodeURIComponent(modelId)}`);
  }

  function saveProviderFields(providerId: string, changed: Record<string, string>) {
    return saveFields(changed, (field) => `/providers/${encodeURIComponent(providerId)}/${field}`);
  }

  function saveLlmRuntime(changed: Record<string, string>) {
    // key 形如 "cache.ttl_seconds": 网关配置分段, 但对用户是同一张表单。
    return saveFields(changed, (key) => `/llm-runtime/${key.replace(".", "/")}`);
  }

  async function chooseCurrentModel(providerId: string, modelId: string) {
    try {
      await api("/models/current", {
        method: "PUT", body: JSON.stringify({ provider_id: providerId, model_id: modelId }),
      });
      await loadModels();
      return true;
    } catch (reason) {
      setError((reason as Error).message);
      return false;
    }
  }

  async function setModelOverride(origin: string, providerId: string, modelId: string) {
    try {
      await api(`/model-overrides/${encodeURIComponent(origin)}`, {
        method: "PUT", body: JSON.stringify({ provider_id: providerId, model_id: modelId }),
      });
      await loadModels();
    } catch (reason) { setError((reason as Error).message); }
  }

  async function clearModelOverride(origin: string) {
    try {
      await api(`/model-overrides/${encodeURIComponent(origin)}`, { method: "DELETE" });
      await loadModels();
    } catch (reason) { setError((reason as Error).message); }
  }

  async function updateThinking(mode: string, effort: string) {
    try {
      const result = await api<{ thinking: ThinkingView }>("/thinking", {
        method: "POST", body: JSON.stringify({ mode, effort }),
      });
      setThinking(result.thinking);
    } catch (reason) { setError((reason as Error).message); }
  }

  async function pruneRules() {
    try {
      await api("/rules/prune", { method: "POST" });
      await loadAdministration();
    } catch (reason) { setError((reason as Error).message); }
  }

  async function undoLatest() {
    try {
      await api("/undo", { method: "POST" });
      await loadAdministration();
    } catch (reason) { setError((reason as Error).message); }
  }

  async function previewCheckpoint(id: string): Promise<string> {
    try {
      const result = await api<Record<string, unknown>>(`/checkpoints/${id}/preview`);
      return JSON.stringify(result, null, 2);
    } catch (reason) {
      return (reason as Error).message;
    }
  }

  async function activatePlanDoc(planId: string) {
    try {
      await api(`/plans/${encodeURIComponent(planId)}/activate`, { method: "POST" });
      await loadWorkspace();
    } catch (reason) { setError((reason as Error).message); }
  }

  if (!activeProjectId) {
    return (
      <main className="project-center">
        <div className="brand-lockup"><span className="forge-mark">F</span><div><h1>Forge</h1><p>本地优先的 AI 工程工作台</p></div></div>
        {error && <div className="error-banner">{error}</div>}
        <section className="project-grid">
          {projects.map((project) => (
            <button className="project-card" key={project.project_id} onClick={() => activate(project.project_id)}>
              <span className="project-icon">⌘</span><strong>{shortPath(project.primary_workspace_root)}</strong>
              <small>{project.primary_workspace_root}</small><span>打开项目 →</span>
            </button>
          ))}
          <form className="project-card add-project" onSubmit={trust}>
            <strong>信任新项目</strong><small>输入本机目录的绝对路径</small>
            <input value={trustPath} onChange={(event) => setTrustPath(event.target.value)} placeholder="/path/to/repository" />
            <button type="submit">添加并打开</button>
          </form>
        </section>
      </main>
    );
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="top-brand"><span className="forge-mark small">F</span><strong>Forge</strong></div>
        <button className="project-picker" onClick={() => setShowProjectPicker((value) => !value)} aria-expanded={showProjectPicker} title={activeProject?.primary_workspace_root}>
          <span>{activeProject ? shortPath(activeProject.primary_workspace_root) : "选择项目"}</span>
          <ChevronIcon className="picker-caret" />
        </button>
        <span className={`connection ${connection}`} title={connectionCopy[connection].hint}>
          <i />
          <span className="connection-label">{connectionCopy[connection].label}</span>
          {retryDelay > 0 && <em>{formatDelay(retryDelay)}</em>}
        </span>
        <button className={`plan-toggle ${showPlan ? "active" : ""}`} onClick={() => setShowPlan((value) => !value)} aria-pressed={showPlan} aria-label={showPlan ? "隐藏计划" : "显示计划"}><PlanIcon />{planning.plan?.status === "proposed" && <i className="plan-badge" title="有计划待评审" />}</button>
        <button className="icon-button" onClick={openSettings} aria-label="设置"><GearIcon /></button>
      </header>
      {showProjectPicker && <ProjectPicker projects={projects} activeProjectId={activeProjectId} busy={busy} trustPath={trustPath} onTrustPath={setTrustPath} onTrust={trust} onActivate={activate} onCancel={() => setShowProjectPicker(false)} />}
      {error && <div className="error-banner workspace-error" role="alert"><span>{error}</span><button onClick={() => setError("")} aria-label="关闭提示">×</button></div>}
      <div className={`workspace-grid ${showPlan ? "with-plan" : ""}`} style={{ "--context-width": `${planWidth}px` } as CSSProperties}>
        <aside className="sidebar">
          <div className="sidebar-title"><span>会话</span><button onClick={createSession}>＋</button></div>
          <nav className="session-list">
            {sessions.map((session) => (
              <button className={session.session_id === currentSessionId ? "active" : ""} key={session.session_id} onClick={() => resumeSession(session.session_id)} disabled={busy && session.session_id !== currentSessionId}>
                <strong title={session.title || "未命名会话"}>{session.title || "未命名会话"}</strong><small>{session.updated_at?.slice(0, 16).replace("T", " ")}</small>
              </button>
            ))}
            {!sessions.length && <p className="empty-copy">发送第一条消息后，会话会出现在这里。</p>}
          </nav>
          <div className="workspace-roots"><span>工作区</span>{activeProject?.workspace_roots.map((root) => <small key={root} title={root}>● {root}</small>)}</div>
        </aside>

        <section className="conversation">
          <div className="timeline" ref={timelineRef} onScroll={handleTimelineScroll}>
            {!transcript.length && !localTurns.length && <div className="welcome"><div className="forge-mark">F</div><h2>准备好了</h2><p>描述你想理解、规划或修改的工程任务。</p></div>}
            {transcript.map((item) => {
              const run = item.payload.role === "assistant" ? restoredByTurn.get(item.payload.turn_id ?? "") : undefined;
              return <Fragment key={item.event_id}>{run && <RunProcess turn={run} />}<Message item={item} /></Fragment>;
            })}
            {localTurns.map((turn) => <LocalTurnView turn={turn} key={turn.clientId} />)}
          </div>
          <div className="conversation-footer">
            {showJumpToBottom && <button className="jump-bottom" onClick={jumpToBottom}>回到底部 ↓</button>}
            {approvals[0] && <ApprovalCard approval={approvals[0]} onResolve={resolveApproval} />}
            <form className={`composer ${connection === "stopped" ? "offline" : ""}`} onSubmit={send}>
              <textarea ref={composerRef} value={message} rows={1} disabled={connection === "stopped"} onChange={(event) => setMessage(event.target.value)} onCompositionStart={() => { composingRef.current = true; }} onCompositionEnd={() => { composingRef.current = false; }} onKeyDown={(event) => { if (shouldSendOnEnter(event.key, event.shiftKey, event.nativeEvent.isComposing, composingRef.current)) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder={connection === "stopped" ? "连不上本地服务，请重新运行 forge 后刷新页面" : "描述你想理解、规划或修改的工程任务…"} />
              <div className="composer-bar">
                <ModeMenu value={mode} disabled={busy} onChange={changeMode} />
                <ModelMenu
                  current={currentModel}
                  models={configuredModels}
                  thinking={thinking}
                  disabled={busy}
                  onChoose={chooseCurrentModel}
                  onThinking={updateThinking}
                />
                <span className="composer-hint">Enter 发送 · Shift+Enter 换行</span>
                {busy
                  ? <button type="button" className="stop" onClick={cancelTurn} disabled={stopping}>{stopping ? "停止中…" : "停止"}</button>
                  : <button type="submit" disabled={!message.trim() || connection === "stopped"}>发送 ↑</button>}
              </div>
            </form>
          </div>
        </section>

        {showPlan && <><div className="context-resizer" onPointerDown={beginPlanResize} title="拖动调整计划栏宽度" /><aside className="context-panel"><div className="panel-heading"><strong>计划</strong><div><button onClick={() => window.open("/api/v1/planning/markdown", "_blank", "noopener,noreferrer")} disabled={!planning.plan}>打开 Markdown</button><button onClick={() => setShowPlan(false)} aria-label="隐藏计划">×</button></div></div><PlanningPanel planning={planning} index={planIndex} onResolve={resolvePlan} onActivate={activatePlanDoc} /></aside></>}
      </div>

      {showSettings && <SettingsPanel
        items={settings} roots={workspaceRoots} rules={rules} checkpoints={checkpoints}
        providerSettings={providerSettings} providerFields={providerFields} modelFields={modelFields}
        providers={providers} knownProviders={knownProviders} llmRuntime={llmRuntime}
        catalog={{ currentModel, overrides, origins, thinking, tools, status: statusView, recovery }}
        actions={{
          onSetCurrentModel: chooseCurrentModel,
          onSetOverride: setModelOverride,
          onClearOverride: clearModelOverride,
          onUpdateThinking: updateThinking,
          onPruneRules: pruneRules,
          onUndo: undoLatest,
          onPreviewCheckpoint: previewCheckpoint,
        }}
        onClose={() => setShowSettings(false)} onSave={saveSetting} onAddRoot={addWorkspace}
        onRemoveRoot={removeWorkspace} onRevokeRule={revokeRule} onRestore={restoreCheckpoint}
        onAddModel={addModel} onRemoveModel={removeModel} onSaveModel={saveModelFields}
        onSaveProvider={saveProviderFields} onSaveLlmRuntime={saveLlmRuntime} />}
    </main>
  );
}

function Message({ item }: { item: TranscriptEvent }) {
  const user = item.payload.role === "user";
  const text = item.payload.text ?? "";
  return <article className={`message ${user ? "user" : "assistant"}`}><div className="avatar">{user ? "你" : "F"}</div><div><strong>{user ? "你" : "Forge"}</strong>{user ? <p>{text}</p> : <><Markdown content={text} /><div className="message-footer"><span /><CopyButton content={text} className="message-copy-outside" /></div></>}</div></article>;
}

function LocalTurnView({ turn }: { turn: LocalTurn }) {
  const text = turn.assistantText || turn.error;
  const metrics = metricsFor(turn.events, Date.now(), turn.startedAt);
  // 占位轮次没有本地用户文本（刷新页面后接上的 turn），用户消息已经在 transcript 里。
  return <section className="local-turn">{turn.userText && <article className="message user"><div className="avatar">你</div><div><strong>你</strong><p>{turn.userText}</p></div></article>}<RunProcess turn={turn} />{text && <article className="message assistant streaming"><div className="avatar">F</div><div><strong>Forge</strong><Markdown content={text} />{turn.status === "running" && <i className="stream-caret" />}<div className="message-footer">{turn.status !== "running" ? <FinalMetrics metrics={metrics} /> : <span />}<CopyButton content={text} className="message-copy-outside" /></div></div></article>}</section>;
}

function FinalMetrics({ metrics }: { metrics: ReturnType<typeof metricsFor> }) {
  const chips: Array<[string, string]> = [
    ["耗时", formatElapsed(metrics.elapsedMs)],
    ["输入", formatTokens(metrics.inputTokens)],
    ["输出", formatTokens(metrics.outputTokens)],
  ];
  if (metrics.reasoningTokens > 0) chips.push(["思考", formatTokens(metrics.reasoningTokens)]);
  if (metrics.cachedTokens > 0) chips.push(["缓存", formatTokens(metrics.cachedTokens)]);
  // 压缩是 Forge 自己发起的调用, 不是用户这句话直接引起的 —— 单列一格, 但仍然算进合计。
  if (metrics.compactTokens > 0) chips.push(["压缩", formatTokens(metrics.compactTokens)]);
  chips.push(["模型", `${metrics.modelCalls} 次`], ["工具", `${metrics.toolCalls} 次`]);
  return <span className="final-metrics">
    {chips.map(([label, value]) => <span className="metric" key={label}><i>{label}</i>{value}</span>)}
    <span className="metric total" title={`${metrics.totalTokens.toLocaleString()} tokens · ${metrics.estimated ? "供应商未回 usage，本轮为本地估算" : "供应商返回的用量"}`}>
      <i>合计</i>{metrics.estimated ? "≈" : ""}{formatTokens(metrics.totalTokens)} tokens
    </span>
  </span>;
}

/** 输入框旁的模型与思考强度入口: 这两项调得最勤, 不该每次都进设置页。 */
function ModelMenu({ current, models, thinking, disabled, onChoose, onThinking }: {
  current: string;
  models: string[];
  thinking: ThinkingView;
  disabled: boolean;
  onChoose: (providerId: string, modelId: string) => void;
  onThinking: (mode: string, effort: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  useEscape(open, useCallback(() => setOpen(false), []));
  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", closeOutside);
    return () => document.removeEventListener("mousedown", closeOutside);
  }, [open]);
  const efforts = thinking.supported_efforts ?? [];
  const thinkingOn = thinking.mode === "on";
  const label = current ? current.split(":").slice(1).join(":") || current : "选择模型";
  return <div className="mode-menu model-menu" ref={rootRef}>
    <button type="button" className="mode-trigger" disabled={disabled} aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen((state) => !state)} title={current || "未设置模型"}>
      <span className="model-label">{label}</span>
      {thinkingOn && <em className="thinking-badge">思考{thinking.effort ? ` · ${thinking.effort}` : ""}</em>}
      <ChevronIcon className="mode-caret" />
    </button>
    {open && <div className="mode-options model-options">
      <p className="menu-heading">模型</p>
      <ul role="listbox" aria-label="当前模型">
        {models.map((ref) => (
          <li key={ref}>
            <button type="button" role="option" aria-selected={ref === current} className={ref === current ? "active" : ""} onClick={() => { setOpen(false); const [provider, ...rest] = ref.split(":"); onChoose(provider, rest.join(":")); }}>
              <span><strong>{ref.split(":").slice(1).join(":") || ref}</strong><small>{ref.split(":")[0]}</small></span>
              {ref === current && <CheckIcon className="mode-check" />}
            </button>
          </li>
        ))}
        {!models.length && <li><p className="empty-copy">还没有已配置的模型，去设置页添加。</p></li>}
      </ul>
      {thinking.configured && <>
        <p className="menu-heading">思考</p>
        <div className="thinking-row">
          <button type="button" className={thinkingOn ? "" : "active"} onClick={() => onThinking("off", "")}>关闭</button>
          <button type="button" className={thinkingOn ? "active" : ""} onClick={() => onThinking("on", "")}>开启</button>
        </div>
        {efforts.length > 0 && <div className="thinking-row">
          {efforts.map((item) => <button type="button" key={item} className={thinking.effort === item ? "active" : ""} onClick={() => onThinking("", item)}>{item}</button>)}
        </div>}
        {!efforts.length && <p className="field-help">该模型未声明可用强度。</p>}
      </>}
    </div>}
  </div>;
}

function ModeMenu({ value, disabled, onChange }: { value: string; disabled: boolean; onChange: (value: string) => void }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  useEscape(open, useCallback(() => setOpen(false), []));
  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", closeOutside);
    return () => document.removeEventListener("mousedown", closeOutside);
  }, [open]);
  const current = modeOptions.find((item) => item.value === value) ?? modeOptions[1];
  return <div className="mode-menu" ref={rootRef}>
    <button type="button" className="mode-trigger" disabled={disabled} aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen((state) => !state)}>
      <i className={`mode-dot ${current.value}`} />
      <span>{current.label}</span>
      <ChevronIcon className="mode-caret" />
    </button>
    {open && <ul className="mode-options" role="listbox" aria-label="运行模式">
      {modeOptions.map((item) => (
        <li key={item.value}>
          <button type="button" role="option" aria-selected={item.value === value} className={item.value === value ? "active" : ""} onClick={() => { setOpen(false); if (item.value !== value) onChange(item.value); }}>
            <i className={`mode-dot ${item.value}`} />
            <span><strong>{item.label}</strong><small>{item.hint}</small></span>
            {item.value === value && <CheckIcon className="mode-check" />}
          </button>
        </li>
      ))}
    </ul>}
  </div>;
}

function formatElapsed(milliseconds: number) {
  return milliseconds >= 1000 ? `${(milliseconds / 1000).toFixed(1)}s` : `${Math.round(milliseconds)}ms`;
}

function PlanningPanel({ planning, index, onResolve, onActivate }: { planning: Planning; index: PlanIndexView | null; onResolve: (decision: string) => void; onActivate: (planId: string) => void }) {
  const plan = planning.plan;
  const todo = planning.todo;
  const others = (index?.plans ?? []).filter((item) => item.plan_id !== index?.active_plan_id);
  if (!plan && !todo && !others.length) {
    return <div className="panel-empty"><span>◇</span><p>当前没有活动计划</p><small>切换到 Plan 模式，让 Forge 先梳理实施方向。</small></div>;
  }
  return <div className="plan-panel">
    {plan && <>
      <span className={`status ${plan.status}`}>{plan.status}</span>
      <Markdown content={planning.markdown ?? `# ${plan.title}\n\n${plan.goal}`} />
      {plan.status === "proposed" && <div className="plan-actions"><button onClick={() => onResolve("reject")}>拒绝</button><button onClick={() => onResolve("approve")}>同意</button><button className="primary" onClick={() => onResolve("approve_and_run")}>同意并执行</button></div>}
    </>}
    {todo?.items?.length ? <section className="todo-block">
      <h4>待办 {todo.items.filter((item) => item.status === "done").length}/{todo.items.length}</h4>
      <ol className="todo-list">
        {todo.items.map((item, position) => <li className={`todo-${item.status}`} key={`${position}-${item.title}`}><i />{item.title}</li>)}
      </ol>
    </section> : null}
    {others.length > 0 && <section className="plan-catalog">
      <h4>其他计划</h4>
      {others.map((item) => <button className="plan-entry" key={item.plan_id} onClick={() => onActivate(item.plan_id)}>
        <strong>{item.title || item.plan_id}</strong><small>{item.plan_id} · r{item.revision} · {item.status}</small>
      </button>)}
    </section>}
  </div>;
}

function ApprovalCard({ approval, onResolve }: { approval: Approval; onResolve: (id: string, decision: string) => void }) {
  const targets = approval.view.target_groups.filter((group) => group.paths.length);
  const canWorkspace = approval.view.allowed_scopes.includes("workspace");
  return <section className="approval-card"><header><span className="warning-icon"><ShieldIcon /></span><div><h2>Forge 想要执行一项操作</h2><p>{approval.mandatory ? "此操作需要逐次确认" : "请确认命令和影响范围是否符合预期"}</p></div></header><details><summary>查看命令与影响范围</summary><pre>{approval.view.raw_command}</pre>{targets.map((group) => <div className="target-group" key={group.label}><strong>{group.label} · {group.paths.length}</strong>{group.paths.map((path) => <code key={path}>{path}</code>)}</div>)}{approval.view.unresolved_reason && <p className="warning-copy">目标集合未封闭：{approval.view.unresolved_reason}</p>}</details><footer><button onClick={() => onResolve(approval.approval_id, "deny")}>拒绝</button>{canWorkspace && <button onClick={() => onResolve(approval.approval_id, "workspace")}>始终允许此工作区</button>}<button className="primary" onClick={() => onResolve(approval.approval_id, "once")}>允许一次</button></footer></section>;
}

function ProjectPicker({ projects, activeProjectId, busy, trustPath, onTrustPath, onTrust, onActivate, onCancel }: { projects: Project[]; activeProjectId: string | null; busy: boolean; trustPath: string; onTrustPath: (path: string) => void; onTrust: (event: FormEvent) => void; onActivate: (id: string) => void; onCancel: () => void }) {
  return <div className="project-switcher-layer" role="dialog" aria-modal="true" aria-label="选择项目"><main className="project-center project-switcher"><button className="project-switcher-close" onClick={onCancel} aria-label="关闭项目选择">×</button><div className="brand-lockup"><span className="forge-mark">F</span><div><h1>选择项目</h1><p>{busy ? "当前请求处理中，暂不能切换项目" : "选择一个已信任项目，或添加本机目录"}</p></div></div><section className="project-grid">{projects.map((project) => <button className={`project-card ${project.project_id === activeProjectId ? "active" : ""}`} disabled={busy || project.project_id === activeProjectId} onClick={() => onActivate(project.project_id)} key={project.project_id}><span className="project-icon">⌘</span><strong>{shortPath(project.primary_workspace_root)}</strong><small>{project.primary_workspace_root}</small><span>{project.project_id === activeProjectId ? "当前项目" : "打开项目 →"}</span></button>)}<form className="project-card add-project" onSubmit={onTrust}><strong>信任新项目</strong><small>输入本机目录的绝对路径</small><input value={trustPath} onChange={(event) => onTrustPath(event.target.value)} placeholder="/path/to/repository" disabled={busy} /><button type="submit" disabled={busy || !trustPath.trim()}>添加并打开</button></form></section><p className="escape-hint">按 Esc 关闭</p></main></div>;
}

type SettingsPanelProps = {
  items: Setting[];
  roots: WorkspaceRoot[];
  rules: LearnedRule[];
  checkpoints: Checkpoint[];
  providers: Provider[];
  providerSettings: Provider[];
  providerFields: FieldSpec[];
  modelFields: FieldSpec[];
  knownProviders: KnownProvider[];
  llmRuntime: LlmRuntimeSettings | null;
  catalog: AdminCatalog;
  actions: AdminActions;
  onClose: () => void;
  onSave: (item: Setting, value: string) => void;
  onAddRoot: (path: string, access: string) => void;
  onRemoveRoot: (path: string) => void;
  onRevokeRule: (id: string) => void;
  onRestore: (id: string) => void;
  onAddModel: (providerId: string, modelId: string, params: Record<string, unknown>) => Promise<boolean>;
  onRemoveModel: (providerId: string, modelId: string) => void;
  onSaveModel: (providerId: string, modelId: string, changed: Record<string, string>) => void;
  onSaveProvider: (providerId: string, changed: Record<string, string>) => void;
  onSaveLlmRuntime: (changed: Record<string, string>) => void;
};

function SettingsPanel({ items, roots, rules, checkpoints, providers, providerSettings, providerFields, modelFields, knownProviders, llmRuntime, catalog, actions, onClose, onSave, onAddRoot, onRemoveRoot, onRevokeRule, onRestore, onAddModel, onRemoveModel, onSaveModel, onSaveProvider, onSaveLlmRuntime }: SettingsPanelProps) {
  const [tab, setTab] = useState<"general" | "models" | "security" | "recovery" | "status">("general");
  const [path, setPath] = useState("");
  const [access, setAccess] = useState("read");
  return <div className="modal-backdrop settings-layer" role="dialog" aria-modal="true" aria-label="设置" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="settings-panel"><header><div><h2>设置</h2><p>应用级与当前项目配置</p></div><button onClick={onClose}>×</button></header><div className="settings-body"><nav><button className={tab === "general" ? "active" : ""} onClick={() => setTab("general")}>常规</button><button className={tab === "models" ? "active" : ""} onClick={() => setTab("models")}>模型</button><button className={tab === "security" ? "active" : ""} onClick={() => setTab("security")}>安全与工具</button><button className={tab === "recovery" ? "active" : ""} onClick={() => setTab("recovery")}>恢复</button><button className={tab === "status" ? "active" : ""} onClick={() => setTab("status")}>状态</button></nav><div className="settings-fields">
    {tab === "general" && items.map((item) => <SettingField key={item.key} item={item} onSave={onSave} />)}
    {tab === "models" && <ModelSettings
      providers={providers} providerSettings={providerSettings} providerFields={providerFields}
      modelFields={modelFields} knownProviders={knownProviders} llmRuntime={llmRuntime}
      catalog={catalog} actions={actions} onAddModel={onAddModel} onRemoveModel={onRemoveModel}
      onSaveModel={onSaveModel} onSaveProvider={onSaveProvider} onSaveLlmRuntime={onSaveLlmRuntime}
    />}
    {tab === "security" && <><h3>工作区目录</h3>{roots.map((root, index) => <div className="admin-row" key={root.path}><div><strong>{root.path}</strong><small>{root.access === "write" ? "可读写" : "只读"}</small></div>{index > 0 && <button onClick={() => onRemoveRoot(root.path)}>移除</button>}</div>)}<div className="add-root"><input value={path} onChange={(event) => setPath(event.target.value)} placeholder="额外目录路径" /><select value={access} onChange={(event) => setAccess(event.target.value)}><option value="read">只读</option><option value="write">读写</option></select><button onClick={() => { if (path.trim()) { onAddRoot(path, access); setPath(""); } }}>添加</button></div><h3>学习规则</h3>{rules.length ? rules.map((rule) => <div className="admin-row" key={rule.rule_id}><div><strong>{rule.label || rule.rule_id}</strong><small>{rule.scope} · {rule.match.mode}</small></div><button onClick={() => onRevokeRule(rule.rule_id)}>撤销</button></div>) : <p className="empty-copy">没有工作区学习规则。</p>}
      <div className="add-root prune-row"><button onClick={actions.onPruneRules}>清理已过期 / 已撤销的规则</button></div>
      <h3>当前模式下模型可见的工具</h3>
      <p className="field-help">工具集合由模式的能力上界决定; 换模式会改变这份清单。</p>
      {catalog.tools.map((tool) => (
        <details className="model-editor tool-entry" key={tool.name}>
          <summary><span><strong>{tool.name}</strong><small>{tool.title}</small></span><small>{tool.declared_capabilities.join(" · ") || "无声明能力"}</small></summary>
          <div className="model-fields"><p className="field-help">{tool.description}</p></div>
        </details>
      ))}
      {!catalog.tools.length && <p className="empty-copy">当前模式下没有可见工具。</p>}</>}
    {tab === "recovery" && <><h3>恢复层状态</h3>
      <div className="admin-row"><div><strong>恢复点总数 {catalog.recovery?.checkpoint_count ?? 0}</strong><small>{catalog.recovery?.pending.length ? `${catalog.recovery.pending.length} 个未收尾事务, 可能已发生部分修改` : "没有未收尾的事务"}</small></div>
        <button onClick={actions.onUndo} disabled={!checkpoints.length}>撤销最近一次</button>
      </div>
      {catalog.recovery?.pending.map((item) => <div className="admin-row warning-row" key={item.checkpoint_id}><div><strong>{item.checkpoint_id}</strong><small>{item.status} · {item.created_at}</small></div><button onClick={() => onRestore(item.checkpoint_id)}>恢复</button></div>)}
      <h3>恢复点</h3>{checkpoints.length ? checkpoints.map((checkpoint) => <CheckpointRow key={checkpoint.checkpoint_id} checkpoint={checkpoint} onRestore={onRestore} onPreview={actions.onPreviewCheckpoint} />) : <p className="empty-copy">当前工作区没有恢复点。</p>}</>}
    {tab === "status" && <><h3>会话状态</h3>
      <div className="status-grid">
        <div><span>会话</span><code>{catalog.status?.session_id || "-"}</code></div>
        <div><span>模式</span><code>{catalog.status?.mode || "-"}</code></div>
        <div><span>当前模型</span><code>{catalog.status?.model || "未设置"}</code></div>
        <div><span>最近事件</span><code>{catalog.status?.last_event_id || "-"}</code></div>
        <div><span>运行中</span><code>{catalog.status?.busy ? "是" : "否"}</code></div>
      </div>
      <h3>可操作目录</h3>
      {(catalog.status?.workspace_roots ?? []).map((root) => <div className="admin-row" key={root}><div><strong>{root}</strong></div></div>)}</>}
  </div></div></section></div>;
}

type ModelSettingsProps = {
  providers: Provider[];
  providerSettings: Provider[];
  providerFields: FieldSpec[];
  modelFields: FieldSpec[];
  knownProviders: KnownProvider[];
  llmRuntime: LlmRuntimeSettings | null;
  catalog: AdminCatalog;
  actions: AdminActions;
  onAddModel: (providerId: string, modelId: string, params: Record<string, unknown>) => Promise<boolean>;
  onRemoveModel: (providerId: string, modelId: string) => void;
  onSaveModel: (providerId: string, modelId: string, changed: Record<string, string>) => void;
  onSaveProvider: (providerId: string, changed: Record<string, string>) => void;
  onSaveLlmRuntime: (changed: Record<string, string>) => void;
};

function ProviderMark({ providerId, label }: { providerId: string; label?: string }) {
  return <span className={`provider-mark provider-${providerId}`}>{(label || providerId).slice(0, 1).toUpperCase()}</span>;
}

function ModelSettings({ providers, providerSettings, providerFields, modelFields, knownProviders, llmRuntime, catalog, actions, onAddModel, onRemoveModel, onSaveModel, onSaveProvider, onSaveLlmRuntime }: ModelSettingsProps) {
  const [view, setView] = useState<"models" | "providers" | "gateway">("models");
  const [showAdd, setShowAdd] = useState(false);
  const configured = useMemo(() => providers.flatMap((provider) => provider.models.map((model) => ({ provider, model, ref: modelRef(provider.id, model.id) }))), [providers]);
  const configuredRefs = configured.map((item) => item.ref);
  const [currentProviderId, currentModelId] = splitModelRef(catalog.currentModel);
  const current = configured.find((item) => item.ref === catalog.currentModel);
  const currentProvider = knownProviders.find((item) => item.id === currentProviderId);
  const overrideCount = Object.keys(catalog.overrides).length;

  return <div className="model-settings">
    <div className="model-subnav" role="tablist" aria-label="模型设置分类">
      <button type="button" role="tab" aria-selected={view === "models"} className={view === "models" ? "active" : ""} onClick={() => setView("models")}>模型</button>
      <button type="button" role="tab" aria-selected={view === "providers"} className={view === "providers" ? "active" : ""} onClick={() => setView("providers")}>供应商</button>
      <button type="button" role="tab" aria-selected={view === "gateway"} className={view === "gateway" ? "active" : ""} onClick={() => setView("gateway")}>网关</button>
    </div>

    {view === "models" && <>
      <div className="model-page-heading">
        <div><h3>模型</h3><p>管理已接入的模型，并指定 Forge 默认使用的模型。</p></div>
        <button type="button" className="primary-action" onClick={() => setShowAdd(true)}>＋ 添加模型</button>
      </div>

      <section className={`current-model-card ${current ? "configured" : ""}`}>
        <ProviderMark providerId={currentProviderId || "none"} label={currentProvider?.label} />
        <div>
          <span>当前默认模型</span>
          <strong>{currentModelId || "尚未设置"}</strong>
          <small>{current ? `${current.provider.name} · 所有未单独指定的任务默认使用` : "添加模型后即可设为默认模型"}</small>
        </div>
        {configuredRefs.length > 0 && <select aria-label="更换默认模型" value={catalog.currentModel} onChange={(event) => { const [providerId, modelId] = splitModelRef(event.target.value); void actions.onSetCurrentModel(providerId, modelId); }}>
          <option value="" disabled>选择模型</option>
          {configuredRefs.map((ref) => <option key={ref} value={ref}>{ref}</option>)}
        </select>}
      </section>

      <section className="configured-models">
        <div className="model-section-heading">
          <div><h4>已添加模型 <span>{configured.length}</span></h4><p>展开模型可编辑参数；修改后统一保存。</p></div>
          <button type="button" onClick={() => setView("providers")}>管理供应商 →</button>
        </div>
        {configured.length
          ? <div className="configured-model-list">{configured.map(({ provider, model, ref }) => <ModelEditor
              key={ref} provider={provider} model={model} fields={modelFields}
              isCurrent={ref === catalog.currentModel} onSave={onSaveModel} onRemove={onRemoveModel}
            />)}</div>
          : <div className="model-empty-state"><ProviderMark providerId="none" label="＋" /><strong>还没有模型</strong><p>添加第一个模型后，Forge 才能开始对话和执行任务。</p><button type="button" className="primary-action" onClick={() => setShowAdd(true)}>添加模型</button></div>}
      </section>

      <details className="model-routing">
        <summary><span><strong>高级路由</strong><small>Thinking 与按任务用途覆盖默认模型</small></span><span>{overrideCount ? `${overrideCount} 项已配置` : "使用默认模型"} <ChevronIcon /></span></summary>
        <div className="model-routing-body">
          <ThinkingEditor thinking={catalog.thinking} onUpdate={actions.onUpdateThinking} />
          <h4>用途模型覆盖</h4>
          <p className="field-help">未设置的用途自动使用当前默认模型。</p>
          {catalog.origins.map((origin) => <div className="admin-row" key={origin}>
            <div><strong>{origin}</strong><small>{catalog.overrides[origin] || "使用默认模型"}</small></div>
            <div className="row-actions">
              <select value={catalog.overrides[origin] ?? ""} onChange={(event) => { const [providerId, modelId] = splitModelRef(event.target.value); actions.onSetOverride(origin, providerId, modelId); }}>
                <option value="" disabled>选择覆盖模型</option>
                {configuredRefs.map((ref) => <option key={ref} value={ref}>{ref}</option>)}
              </select>
              <button onClick={() => actions.onClearOverride(origin)} disabled={!catalog.overrides[origin]}>清除</button>
            </div>
          </div>)}
        </div>
      </details>
    </>}

    {view === "providers" && <>
      <div className="model-page-heading"><div><h3>供应商</h3><p>配置兼容端点与密钥环境变量；API 密钥不会在页面中读取或保存。</p></div></div>
      <div className="provider-availability-list">{knownProviders.map((known) => {
        const provider = providerSettings.find((item) => item.id === known.id);
        if (!provider) return null;
        return <ProviderEditor key={known.id} provider={provider} known={known} fields={providerFields} onSaveProvider={onSaveProvider} />;
      })}</div>
      <p className="field-help provider-protocol-help">仅支持 OpenAI Compatible <code>/chat/completions</code> 协议；Ollama、vLLM 等本地端点使用 Local。</p>
    </>}

    {view === "gateway" && <>
      <div className="model-page-heading"><div><h3>网关治理</h3><p>控制缓存、重试与故障熔断；通常保持默认值即可。</p></div></div>
      {llmRuntime && <DraftForm fields={[
        { key: "cache.enabled", label: "缓存开关", value: String(llmRuntime.cache.enabled), choices: ["false", "true"] },
        { key: "cache.ttl_seconds", label: "缓存 TTL（秒）", value: String(llmRuntime.cache.ttl_seconds ?? "") },
        { key: "cache.max_entries", label: "缓存条目上限", value: String(llmRuntime.cache.max_entries) },
        { key: "cache.origins", label: "缓存 origins", value: llmRuntime.cache.origins.join(",") },
        { key: "circuit_breaker.enabled", label: "熔断开关", value: String(llmRuntime.circuit_breaker.enabled), choices: ["false", "true"] },
        { key: "circuit_breaker.failure_threshold", label: "失败阈值", value: String(llmRuntime.circuit_breaker.failure_threshold) },
        { key: "circuit_breaker.cooldown_seconds", label: "冷却时间（秒）", value: String(llmRuntime.circuit_breaker.cooldown_seconds) },
        { key: "retry.wait_threshold_seconds", label: "429 等待阈值（秒）", value: String(llmRuntime.retry.wait_threshold_seconds) },
      ]} onSave={onSaveLlmRuntime} />}
    </>}

    {showAdd && <AddModelDrawer
      knownProviders={knownProviders} providerSettings={providerSettings}
      onClose={() => setShowAdd(false)} onEditProvider={() => { setShowAdd(false); setView("providers"); }}
      onAdd={onAddModel} onSetCurrent={actions.onSetCurrentModel}
    />}
  </div>;
}

function AddModelDrawer({ knownProviders, providerSettings, onClose, onEditProvider, onAdd, onSetCurrent }: {
  knownProviders: KnownProvider[];
  providerSettings: Provider[];
  onClose: () => void;
  onEditProvider: () => void;
  onAdd: (providerId: string, modelId: string, params: Record<string, unknown>) => Promise<boolean>;
  onSetCurrent: (providerId: string, modelId: string) => Promise<boolean>;
}) {
  const [providerId, setProviderId] = useState(knownProviders[0]?.id ?? "");
  const [modelId, setModelId] = useState("");
  const [contextWindow, setContextWindow] = useState("");
  const [maxTokens, setMaxTokens] = useState("");
  const [makeDefault, setMakeDefault] = useState(true);
  const [saving, setSaving] = useState(false);
  const [validationError, setValidationError] = useState("");
  const selected = knownProviders.find((item) => item.id === providerId);
  const selectedSettings = providerSettings.find((item) => item.id === providerId);

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopImmediatePropagation();
      onClose();
    };
    window.addEventListener("keydown", closeOnEscape, true);
    return () => window.removeEventListener("keydown", closeOnEscape, true);
  }, [onClose]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!providerId || !modelId.trim() || saving) return;
    let params: Record<string, unknown>;
    try {
      params = buildInitialModelParams(contextWindow, maxTokens);
      setValidationError("");
    } catch (reason) {
      setValidationError((reason as Error).message);
      return;
    }
    setSaving(true);
    const created = await onAdd(providerId, modelId.trim(), params);
    if (created && makeDefault) await onSetCurrent(providerId, modelId.trim());
    setSaving(false);
    if (created) onClose();
  }

  return <div className="model-drawer-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <aside className="model-drawer" role="dialog" aria-modal="true" aria-label="添加模型">
      <header><div><h3>添加模型</h3><p>先选择供应商，再填写模型 ID。</p></div><button type="button" aria-label="关闭添加模型" onClick={onClose}>×</button></header>
      <form onSubmit={submit}>
        <fieldset><legend>1. 选择供应商</legend><div className="provider-choice-grid">{knownProviders.map((provider) => <button
          type="button" key={provider.id} className={provider.id === providerId ? "active" : ""}
          aria-pressed={provider.id === providerId} onClick={() => { setProviderId(provider.id); setValidationError(""); }}
        ><ProviderMark providerId={provider.id} label={provider.label} /><span><strong>{provider.label}</strong><small className={provider.available || !provider.api_key_env ? "ready" : ""}>{providerAvailabilityCopy(provider)}</small></span><CheckIcon /></button>)}</div></fieldset>

        <fieldset><legend>2. 模型信息</legend>
          <label className="model-input"><span>模型 ID</span><input autoFocus value={modelId} onChange={(event) => setModelId(event.target.value)} placeholder={modelPlaceholder(providerId)} /><small>填写供应商 API 使用的准确模型名称。</small></label>
          <div className={`provider-connection-summary ${selected?.available || !selected?.api_key_env ? "ready" : "warning"}`}>
            <span><i /> <strong>{selected?.label || "供应商"}</strong> · {providerAvailabilityCopy(selected ?? { available: false })}{selectedSettings?.api_base ? ` · ${selectedSettings.api_base}` : ""}</span>
            <button type="button" onClick={onEditProvider}>编辑连接配置</button>
          </div>
        </fieldset>

        <details className="model-add-advanced"><summary>高级参数 <span>添加后也可以修改 <ChevronIcon /></span></summary><div>
          <label><span>上下文窗口</span><input inputMode="numeric" value={contextWindow} onChange={(event) => setContextWindow(event.target.value)} placeholder="使用供应商默认值" /></label>
          <label><span>最大输出 Tokens</span><input inputMode="numeric" value={maxTokens} onChange={(event) => setMaxTokens(event.target.value)} placeholder="使用供应商默认值" /></label>
        </div></details>
        {validationError && <p className="model-validation-error" role="alert">{validationError}</p>}
        <label className="model-default-check"><input type="checkbox" checked={makeDefault} onChange={(event) => setMakeDefault(event.target.checked)} /><span>添加后设为默认模型</span></label>
        <footer><button type="button" onClick={onClose}>取消</button><button type="submit" className="primary-action" disabled={!providerId || !modelId.trim() || saving}>{saving ? "添加中…" : makeDefault ? "添加并使用" : "添加模型"}</button></footer>
      </form>
    </aside>
  </div>;
}

function ThinkingEditor({ thinking, onUpdate }: { thinking: ThinkingView; onUpdate: (mode: string, effort: string) => void }) {
  if (!thinking.configured) {
    return <p className="field-help">选择当前模型后可以在这里调 thinking 开关与强度。</p>;
  }
  const efforts = thinking.supported_efforts ?? [];
  return <div className="admin-row">
    <div><strong>Thinking · {thinking.model}</strong><small>只影响当前 Forge 进程, 不写入模型配置</small></div>
    <div className="row-actions">
      <select value={thinking.mode ?? "off"} onChange={(event) => onUpdate(event.target.value, "")}>
        <option value="off">off</option><option value="on">on</option>
      </select>
      <select value={thinking.effort ?? ""} onChange={(event) => onUpdate("", event.target.value)} disabled={!efforts.length}>
        <option value="" disabled>{efforts.length ? "选择强度" : "该模型未声明强度"}</option>
        {efforts.map((item) => <option key={item} value={item}>{item}</option>)}
      </select>
    </div>
  </div>;
}

function CheckpointRow({ checkpoint, onRestore, onPreview }: { checkpoint: Checkpoint; onRestore: (id: string) => void; onPreview: (id: string) => Promise<string> }) {
  const [preview, setPreview] = useState("");
  return <div className="admin-row checkpoint-row">
    <div>
      <strong>{checkpoint.checkpoint_id}</strong>
      <small>{checkpoint.status} · {checkpoint.snapshot_strategy} · {checkpoint.created_at}</small>
      {preview && <pre className="checkpoint-preview">{preview}</pre>}
    </div>
    <div className="row-actions">
      <button onClick={() => { if (preview) { setPreview(""); return; } onPreview(checkpoint.checkpoint_id).then(setPreview); }}>{preview ? "收起" : "预览"}</button>
      <button onClick={() => onRestore(checkpoint.checkpoint_id)}>恢复</button>
    </div>
  </div>;
}

function ProviderEditor({ provider, known, fields, onSaveProvider }: { provider: Provider; known: KnownProvider; fields: FieldSpec[]; onSaveProvider: (providerId: string, changed: Record<string, string>) => void }) {
  // 表单行由后端给（从 ProviderConfig 的字段声明派生）。这里曾经硬编码过一份同样的
  // (字段名, 中文标签) 数组——加一个字段时它不会报错，只会在页面上少一行。
  const rows = fields.map((field) => ({
    key: field.name,
    label: field.label,
    value: String((provider as unknown as Record<string, unknown>)[field.name] ?? ""),
  }));
  const availability = providerAvailabilityCopy(known);
  return <details className="provider-editor">
    <summary className="provider-heading"><ProviderMark providerId={provider.id} label={provider.name} /><span><strong>{provider.name}</strong><small>{provider.api_base || "尚未配置 API 端点"}</small></span><em className={known.available || !known.api_key_env ? "ready" : ""}>{availability}</em><ChevronIcon /></summary>
    <DraftForm className="provider-fields" fields={rows} onSave={(changed) => onSaveProvider(provider.id, changed)} />
  </details>;
}

function ModelEditor({ provider, model, fields, isCurrent, onSave, onRemove }: { provider: Provider; model: Model; fields: FieldSpec[]; isCurrent: boolean; onSave: (providerId: string, modelId: string, changed: Record<string, string>) => void; onRemove: (providerId: string, modelId: string) => void }) {
  const [confirmRemove, setConfirmRemove] = useState(false);
  return <details className="model-editor">
    <summary><ProviderMark providerId={provider.id} label={provider.name} /><span><strong>{model.id}{isCurrent && <em>默认</em>}</strong><small>{provider.name} · {model.params.context_window ? `${model.params.context_window} 上下文` : "使用供应商默认参数"}</small></span><span className="model-row-status"><i /> 可用</span><ChevronIcon /></summary>
    <DraftForm
      className="model-fields"
      fields={[
        { key: "thinking_mode", label: "Thinking", value: model.params.thinking_mode ?? "off", choices: ["off", "on"] },
        ...fields.map((field) => ({ key: field.name, label: field.label, value: String(model.params[field.name] ?? "") })),
        { key: "thinking_efforts", label: "Thinking 强度（逗号分隔）", value: (model.params.thinking_efforts ?? []).join(",") },
        { key: "thinking_default_effort", label: "默认 Thinking 强度", value: String(model.params.thinking_default_effort ?? "") },
        { key: "extra", label: "厂商扩展 JSON", value: JSON.stringify(model.params.extra ?? {}) },
      ]}
      onSave={(changed) => onSave(provider.id, model.id, changed)}
    />
    <div className="model-remove-row"><span>{confirmRemove ? "再次点击将移除此模型" : "移除后不会删除供应商连接配置"}</span><button type="button" className={confirmRemove ? "confirm" : ""} onClick={() => { if (!confirmRemove) { setConfirmRemove(true); return; } onRemove(provider.id, model.id); }}>{confirmRemove ? "确认移除" : "移除模型"}</button>{confirmRemove && <button type="button" onClick={() => setConfirmRemove(false)}>取消</button>}</div>
  </details>;
}

type DraftField = { key: string; label: string; value: string; choices?: string[] };

/**
 * 一组字段一起改、一次保存。
 *
 * 逐项保存的问题不只是点击次数: 每次保存都要重新拉一遍配置, 而重新拉配置会把同一张表单里
 * 其他还没保存的输入冲掉 —— 用户填了三格, 保存第一格, 另外两格就没了。
 */
function DraftForm({ fields, onSave, className = "runtime-settings" }: { fields: DraftField[]; onSave: (changed: Record<string, string>) => void; className?: string }) {
  const committed = useMemo(() => Object.fromEntries(fields.map((field) => [field.key, field.value])), [fields]);
  const signature = fields.map((field) => `${field.key}=${field.value}`).join("\u0001");
  const [draft, setDraft] = useState<Record<string, string>>(committed);
  // 服务端的值变了 (保存成功, 或别处改动后刷新) 才重置草稿, 不在每次渲染时覆盖输入。
  useEffect(() => { setDraft(Object.fromEntries(signature.split("\u0001").map((pair) => { const at = pair.indexOf("="); return [pair.slice(0, at), pair.slice(at + 1)]; }))); }, [signature]);
  const changed = Object.fromEntries(Object.entries(draft).filter(([key, value]) => committed[key] !== value));
  const dirty = Object.keys(changed).length;
  return <div className={`draft-form ${className}`}>
    {fields.map((field) => <label className={`editable-value ${draft[field.key] !== committed[field.key] ? "dirty" : ""}`} key={field.key}>
      <span>{field.label}</span>
      {field.choices
        ? <select value={draft[field.key] ?? ""} onChange={(event) => setDraft((state) => ({ ...state, [field.key]: event.target.value }))}>{field.choices.map((choice) => <option key={choice} value={choice}>{choice}</option>)}</select>
        : <input value={draft[field.key] ?? ""} onChange={(event) => setDraft((state) => ({ ...state, [field.key]: event.target.value }))} />}
    </label>)}
    <div className="draft-actions">
      <span>{dirty ? `${dirty} 项待保存` : "没有改动"}</span>
      <button onClick={() => setDraft(committed)} disabled={!dirty}>撤销</button>
      <button className="primary" onClick={() => onSave(changed)} disabled={!dirty}>保存</button>
    </div>
  </div>;
}

function SettingField({ item, onSave }: { item: Setting; onSave: (item: Setting, value: string) => void }) {
  const [value, setValue] = useState(item.value);
  useEffect(() => setValue(item.value), [item.value]);
  const scope = item.level === "app" ? "所有项目" : "当前项目";
  // 布尔项用是/否下拉，而不是让用户往输入框里敲 "true"。它的取值只有两个，
  // 而一个只能靠背字面量才填得对的输入框，等于把校验推给用户。
  const control = item.kind === "bool"
    ? <select value={value} onChange={(event) => { setValue(event.target.value); onSave(item, event.target.value); }}>
        <option value="true">是</option>
        <option value="false">否</option>
      </select>
    : item.choices.length
      ? <select value={value} onChange={(event) => { setValue(event.target.value); onSave(item, event.target.value); }}>{item.choices.map((choice) => <option key={choice}>{choice}</option>)}</select>
      : <div><input value={value} onChange={(event) => setValue(event.target.value)} /><button onClick={() => onSave(item, value)}>保存</button></div>;
  // 说明放在左列名字底下，而不是作为第三个子元素：.setting-field 是
  // `justify-content: space-between` 的两列 flex，多一个子元素会把控件挤到中间，
  // 名字和说明各自换行，整行散掉。
  return <label className="setting-field">
    <span>
      <strong>{item.label || item.key}</strong>
      <small>{scope}</small>
      {item.help && <small className="setting-help">{item.help}</small>}
    </span>
    {control}
  </label>;
}

export default App;
