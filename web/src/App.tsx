/** 外壳与装配。
 *
 * 这里只做三件事: 调各功能的 hook, 把事件流接到它们身上, 摆布局 (ADR-0048 决策 5)。
 * 任何"某个功能内部怎么做"的逻辑都不该回到这个文件 —— 它一旦开始长, 就会变回那个
 * 谁都不敢改的根组件。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button, Flex, Layout, Splitter } from "antd";
import { VerticalAlignBottomOutlined } from "@ant-design/icons";
import { Composer } from "@/app/Composer";
import { PlanDock } from "@/app/PlanDock";
import { ProjectLanding } from "@/app/ProjectLanding";
import { SessionSidebar } from "@/app/SessionSidebar";
import { TopBar } from "@/app/TopBar";
import { useStatusNotifications } from "@/app/useStatusNotifications";
import { useAdministration } from "@/features/administration/useAdministration";
import { ProjectPicker } from "@/features/chrome/ProjectPicker";
import { ConversationTimeline } from "@/features/conversation/ConversationTimeline";
import { CONTENT_WIDTH } from "@/features/conversation/Message";
import { useConversation } from "@/features/conversation/useConversation";
import { useSessionActions } from "@/features/conversation/useSessionActions";
import { useTimelineScroll } from "@/features/conversation/useTimelineScroll";
import { PromptCard } from "@/features/humanInteraction/PromptCard";
import { usePrompts } from "@/features/humanInteraction/usePrompts";
import { usePlanning } from "@/features/planning/usePlanning";
import { useProjects } from "@/features/projects/useProjects";
import { useProjectSwitch } from "@/features/projects/useProjectSwitch";
import { useRequestActivity } from "@/features/requests/useRequestActivity";
import { useResumePosition } from "@/features/runEvents/resumePosition";
import { useRunEventWiring } from "@/app/useRunEventWiring";
import { SettingsPanel } from "@/features/settings/SettingsPanel";
import { useSettings } from "@/features/settings/useSettings";
import { api, setCsrfToken } from "@/shared/api/client";

function App() {
  const [error, setError] = useState("");
  const dismissError = useCallback(() => setError(""), []);
  const [showSettings, setShowSettings] = useState(false);
  const [showPlan, setShowPlan] = useState(false);
  // 计划栏的宽度受控: 面板是后挂上去的, 不受控时 Splitter 会把两栏对半分。
  const [planWidth, setPlanWidth] = useState(340);
  const [showProjectPicker, setShowProjectPicker] = useState(false);
  // 已经自动弹出过计划栏的那份提案; 同一份不弹第二次。
  const announced = useRef("");
  const refreshHandle = useRef(0);
  // 在途请求由客户端统一记账 (shared/api/client), 这里只订阅结果。
  const requests = useRequestActivity();
  const resumePosition = useResumePosition();
  useStatusNotifications({ requests, error, onDismissError: dismissError });

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

  const { connection, retryDelay, discardPending } = useRunEventWiring({
    projectId: activeProjectId,
    resumePosition,
    conversation,
    reloadPrompts: loadPrompts,
    reloadProjects: loadProjects,
    refreshWorkspace,
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
        trustPath={projects.trustPath}
        onTrustPath={projects.setTrustPath}
        onActivate={projectSwitch.activate}
        onTrust={projectSwitch.trust}
      />
    );
  }

  return (
    <Layout style={{ height: "100vh" }}>
      {/* 在途请求只在窗口顶部拉一条细线: 它不占布局, 也不必让每个按钮各自转圈。 */}
      {requests.busy && <div className="request-bar" role="progressbar" aria-label="正在请求" />}
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
      <Layout>
        <SessionSidebar
          sessions={conversation.sessions}
          currentSessionId={currentSessionId}
          busy={conversation.busy}
          deleting={sessionActions.deleting}
          activeProject={projects.activeProject}
          onCreate={sessionActions.create}
          onResume={sessionActions.resume}
          onDelete={sessionActions.remove}
        />
        <Layout.Content style={{ minWidth: 0 }}>
          <Splitter onResize={(sizes) => setPlanWidth(sizes[1] ?? planWidth)}>
            <Splitter.Panel min={360}>
              <Flex vertical style={{ height: "100%" }}>
                <ConversationTimeline
                  scrollRef={scroll.timelineRef}
                  onScroll={scroll.handleScroll}
                  onContentChange={scroll.stickToBottom}
                  transcript={conversation.transcript}
                  localTurns={conversation.localTurns}
                  restoredByTurn={conversation.restoredByTurn}
                  directory={admin.toolDirectory}
                />
                {/* 与时间线同一套左右留白 + 同一个正文宽度: 输入框两边要和消息对齐。 */}
                <div style={{ padding: "0 clamp(22px, 6vw, 80px) 16px" }}>
                  <Flex vertical gap="small" style={{ maxWidth: CONTENT_WIDTH, margin: "0 auto" }}>
                    {scroll.showJumpToBottom && (
                      <Flex justify="center">
                        <Button
                          size="small"
                          shape="round"
                          icon={<VerticalAlignBottomOutlined />}
                          onClick={scroll.jumpToBottom}
                        >
                          回到底部
                        </Button>
                      </Flex>
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
                  </Flex>
                </div>
              </Flex>
            </Splitter.Panel>
            {showPlan ? (
              <Splitter.Panel size={planWidth} min={260} max={680}>
                <PlanDock
                  onClose={() => setShowPlan(false)}
                  planning={planningFeature.planning}
                  index={planningFeature.index}
                  onResolve={planningFeature.resolveReview}
                  onActivate={planningFeature.activatePlan}
                />
              </Splitter.Panel>
            ) : null}
          </Splitter>
        </Layout.Content>
      </Layout>

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
      {showSettings && <SettingsPanel admin={admin} settings={settingsFeature} onClose={closeSettings} />}
    </Layout>
  );
}

export default App;
