/** 外壳与装配。
 *
 * 这里只做三件事: 调各功能的 hook, 把事件流接到它们身上, 摆布局 (ADR-0048 决策 5)。
 * 任何"某个功能内部怎么做"的逻辑都不该回到这个文件 —— 它一旦开始长, 就会变回那个
 * 谁都不敢改的根组件。
 */

import { CSSProperties, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Composer } from "@/app/Composer";
import { PlanDock } from "@/app/PlanDock";
import { ProjectLanding } from "@/app/ProjectLanding";
import { SessionSidebar } from "@/app/SessionSidebar";
import { StatusBanners } from "@/app/StatusBanners";
import { TopBar } from "@/app/TopBar";
import { useAdministration } from "@/features/administration/useAdministration";
import { ConfirmDialog } from "@/features/chrome/ConfirmDialog";
import { ProjectPicker } from "@/features/chrome/ProjectPicker";
import { ConversationTimeline } from "@/features/conversation/ConversationTimeline";
import { useConversation } from "@/features/conversation/useConversation";
import { useSessionActions } from "@/features/conversation/useSessionActions";
import { useTimelineScroll } from "@/features/conversation/useTimelineScroll";
import { PromptCard } from "@/features/humanInteraction/PromptCards";
import { usePrompts } from "@/features/humanInteraction/usePrompts";
import { usePlanning } from "@/features/planning/usePlanning";
import { useProjects } from "@/features/projects/useProjects";
import { useProjectSwitch } from "@/features/projects/useProjectSwitch";
import { useRequestActivity } from "@/features/requests/useRequestActivity";
import { useResumePosition } from "@/features/runEvents/resumePosition";
import { useRunEvents } from "@/features/runEvents/useRunEvents";
import { SettingsPanel } from "@/features/settings/SettingsPanel";
import { useSettings } from "@/features/settings/useSettings";
import { api, setCsrfToken } from "@/shared/api/client";
import { useEscape } from "@/shared/hooks/useEscape";
import { isTerminalEvent } from "@/shared/lib/run/events";
import { appendRunEvent } from "@/shared/lib/run/turn";

function App() {
  const [error, setError] = useState("");
  const [showSettings, setShowSettings] = useState(false);
  const [showPlan, setShowPlan] = useState(false);
  const [planWidth, setPlanWidth] = useState(320);
  const [showProjectPicker, setShowProjectPicker] = useState(false);
  // 已经自动弹出过计划栏的那份提案; 同一份不弹第二次。
  const announced = useRef("");
  const refreshHandle = useRef(0);
  // 在途请求由客户端统一记账 (shared/api/client), 这里只订阅结果。
  const requests = useRequestActivity();
  const resumePosition = useResumePosition();

  // 每个功能自己持有状态与动作 (ADR-0048 决策 5)。
  const projects = useProjects();
  const { activeProjectId } = projects;
  const promptsFeature = usePrompts(setError);
  const settingsFeature = useSettings(setError);
  const planningFeature = usePlanning(setError);
  const admin = useAdministration(setError, projects.load);
  const loadProjects = projects.load;
  const loadPrompts = promptsFeature.load;

  const closePicker = useCallback(() => setShowProjectPicker(false), []);
  const closeSettings = useCallback(() => setShowSettings(false), []);

  const scroll = useTimelineScroll();
  const conversation = useConversation(setError, resumePosition, scroll.followNow);
  const { currentSessionId, loadTranscript, loadConversation } = conversation;

  const configuredModels = useMemo(
    () => admin.providers.flatMap((provider) => provider.models.map((model) => `${provider.id}:${model.id}`)),
    [admin.providers],
  );

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

  const refreshWorkspace = useCallback(() => {
    // 工作区刷新是 5 个并行请求；密集事件下不节流会把浏览器的连接数吃光。
    if (refreshHandle.current) return;
    refreshHandle.current = window.setTimeout(() => {
      refreshHandle.current = 0;
      loadWorkspace().catch(() => undefined);
    }, 300);
  }, [loadWorkspace]);

  useEffect(
    () => () => {
      if (refreshHandle.current) window.clearTimeout(refreshHandle.current);
    },
    [],
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

  useEscape(showProjectPicker, closePicker);
  useEscape(showSettings, closeSettings);

  // 事件流自己管连接, 退避与合帧; 这里只说"到了之后干什么" (ADR-0048 决策 5)。
  const { connection, retryDelay, discardPending } = useRunEvents(activeProjectId, resumePosition, {
    // 事件缓冲跨会话共用: 重连补发时会带上切换之前那个会话的尾巴。两个会话都有
    // turn_0001, 不按归属丢掉就会叠进当前时间线 (ADR-0048 决策 2)。
    accepts: (event) => {
      const scope = conversation.sessionScopeRef.current;
      return !scope || !event.session_id || event.session_id === scope;
    },
    onBatch: (batch) => {
      conversation.setLocalTurns((items) =>
        batch.reduce((carry, event) => appendRunEvent(carry, event), items),
      );
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
        conversation.setBusy(false);
        refreshWorkspace();
        window.setTimeout(() => conversation.syncFinishedTurn(event.turn_id), 60);
      }
      if (["approval_requested", "approval_resolved", "plan_proposed", "todo_updated"].includes(event.kind)) {
        refreshWorkspace();
      }
    },
    onResync: () => {
      // 位置已经被 hook 丢掉了; 这里负责重新取一次快照, 由快照带回新水位。
      const scope = conversation.sessionScopeRef.current;
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

  const sessionActions = useSessionActions({
    conversation,
    discardPending,
    reloadWorkspace: loadWorkspace,
    onError: setError,
  });
  const projectSwitch = useProjectSwitch({
    projects,
    busy: conversation.busy,
    clearTimeline: conversation.clearTimeline,
    closePicker,
    onError: setError,
  });

  useEffect(() => {
    // 计划待评审时决议按钮只在计划栏里，所以新提案必须自己弹出来一次。
    const plan = planningFeature.planning.plan;
    if (!plan || plan.status !== "proposed" || announced.current === plan.plan_id) return;
    announced.current = plan.plan_id;
    setShowPlan(true);
  }, [planningFeature.planning.plan]);

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
      <ProjectLanding
        projects={projects.projects}
        error={error}
        trustPath={projects.trustPath}
        onTrustPath={projects.setTrustPath}
        onActivate={projectSwitch.activate}
        onTrust={projectSwitch.trust}
      />
    );
  }

  return (
    <main className="app-shell">
      <TopBar
        activeProject={projects.activeProject}
        pickerOpen={showProjectPicker}
        onTogglePicker={() => setShowProjectPicker((value) => !value)}
        connection={connection}
        retryDelay={retryDelay}
        planOpen={showPlan}
        planProposed={planningFeature.planning.plan?.status === "proposed"}
        onTogglePlan={() => setShowPlan((value) => !value)}
        onOpenSettings={openSettings}
      />
      {showProjectPicker && (
        <ProjectPicker
          projects={projects.projects}
          activeProjectId={activeProjectId}
          busy={conversation.busy}
          trustPath={projects.trustPath}
          onTrustPath={projects.setTrustPath}
          onTrust={projectSwitch.trust}
          onActivate={projectSwitch.activate}
          onCancel={closePicker}
        />
      )}
      <StatusBanners requests={requests} error={error} onDismissError={() => setError("")} />
      <div
        className={`workspace-grid ${showPlan ? "with-plan" : ""}`}
        style={{ "--context-width": `${planWidth}px` } as CSSProperties}
      >
        <SessionSidebar
          sessions={conversation.sessions}
          currentSessionId={currentSessionId}
          busy={conversation.busy}
          activeProject={projects.activeProject}
          onCreate={sessionActions.create}
          onResume={sessionActions.resume}
          onRequestDelete={sessionActions.setConfirmDelete}
        />

        <section className="conversation">
          <ConversationTimeline
            scrollRef={scroll.timelineRef}
            onScroll={scroll.handleScroll}
            onContentChange={scroll.stickToBottom}
            transcript={conversation.transcript}
            localTurns={conversation.localTurns}
            restoredByTurn={conversation.restoredByTurn}
            directory={admin.toolDirectory}
          />
          <div className="conversation-footer">
            {scroll.showJumpToBottom && (
              <button className="jump-bottom" onClick={scroll.jumpToBottom}>
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
            <Composer
              value={conversation.message}
              onChange={conversation.setMessage}
              onSend={conversation.send}
              connection={connection}
              busy={conversation.busy}
              stopping={conversation.stopping}
              onCancel={conversation.cancelTurn}
              stance={conversation.stance}
              onStance={conversation.changeStance}
              currentModel={admin.currentModel}
              models={configuredModels}
              thinking={admin.thinking}
              onChooseModel={admin.chooseCurrentModel}
              onThinking={admin.updateThinking}
            />
          </div>
        </section>

        {showPlan && (
          <PlanDock
            width={planWidth}
            onWidth={setPlanWidth}
            onClose={() => setShowPlan(false)}
            planning={planningFeature.planning}
            index={planningFeature.index}
            onResolve={planningFeature.resolveReview}
            onActivate={planningFeature.activatePlan}
          />
        )}
      </div>

      {sessionActions.confirmDelete && (
        <ConfirmDialog
          title="删除这个会话？"
          body={`「${conversation.sessions.find((item) => item.session_id === sessionActions.confirmDelete)?.title || "未命名会话"}」的计划、待办、处理过程与恢复点会一起删掉，这个会话做过的改动将无法撤销。工作区里的文件不受影响。`}
          confirmLabel="删除"
          busy={sessionActions.deleting}
          onConfirm={() => sessionActions.remove(sessionActions.confirmDelete)}
          onCancel={() => sessionActions.setConfirmDelete("")}
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
          onClose={closeSettings}
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
