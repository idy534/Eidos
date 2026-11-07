import { createRoot } from "react-dom/client";
import { useState } from "react";
import type { GoalView, SessionGoal } from "../../shared/goals.js";
import type { ApprovalMode, ModelListResult, Run } from "../src/contracts.js";
import { GoalPanel } from "../src/components/goals/GoalPanel.js";
import { Composer, type ComposerWorkMode } from "../src/components/Composer.js";
import { InputContextProvider } from "../src/components/InputContext.js";
import "../src/styles.css";
import "./goals.css";

const sessionId = "11111111-1111-1111-1111-111111111111";
const original: SessionGoal = { id: "22222222-2222-2222-2222-222222222222", sessionId, objective: "完成登录模块的全部修复，并确认相关测试通过。", status: "active",
  revision: 1, objectiveRevision: 1, activation: 1, model: { modelId: "model" }, approvalMode: "full_access", createdAt: 1, updatedAt: 1 };
const models: ModelListResult = { defaultModelId: "model", models: [{ id: "model", name: "DeepSeek-V4.1-Flash", vendor: "DeepSeek", provider: "deepseek",
  url: "https://api.deepseek.com", supportsToolCall: true, supportsImages: false, supportsReasoning: false }] };
const noop = () => {};
declare global { interface Window { goalTest: { state: (value: string) => void }; } }
window.eidosRuntime = {
  listSkills: async () => ({ skills: [] }), listPlugins: async () => ({ plugins: [] }), listMcpServers: async () => ({ servers: [] }),
  listSessions: async () => ({ items: [] }), listAgentTypes: async () => ({ agents: [] }),
  listWorkspaceDirectory: async () => ({ path: ".", entries: [], truncated: false }), onInputQuote: () => noop,
} as unknown as typeof window.eidosRuntime;

function Preview() {
  const [view, setView] = useState<GoalView>({});
  const [mode, setMode] = useState<ComposerWorkMode>("execute");
  const [input, setInput] = useState("完成登录模块的全部修复，并确认相关测试通过。");
  const [access, setAccess] = useState<ApprovalMode>("full_access");
  const [activeRun, setActiveRun] = useState<Run>();
  const changeMode = (next: ComposerWorkMode) => {
    if (next !== "goal" && view.goal?.status === "active") setView({ ...view, goal: { ...view.goal, status: "paused" } });
    setMode(next);
  };
  window.goalTest = { state: (state) => {
    setView({ goal: { ...original, status: state === "waiting_approval" ? "active" : state as SessionGoal["status"],
      ...(state === "complete" ? { statusDetail: "登录模块已完成修复，相关测试全部通过。", evidence: [{ requirement: "登录回归", proof: "相关测试退出码为零。" }] } : {}),
      ...(state === "blocked" ? { statusReason: "idle_runs" } : {}),
    }, tokensUsed: 42600, runCount: 4, effectiveMs: 361000 });
    setMode(state === "paused" ? "execute" : "goal");
    setActiveRun(state === "waiting_approval" ? { id: "run", sessionId, status: "waiting_approval" } as Run : undefined);
  } };
  return <main className="goal-preview"><header><strong>登录模块修复</strong><span>Eidos · 目标模式</span></header>
    <article><p>我已修复登录失败后的状态处理。相关回归测试正在完成最后检查。</p></article>
    <div className="goal-preview__composer"><GoalPanel view={view} ready busy={false} error={undefined} activeRun={activeRun}
      onAction={(action) => { if (view.goal) { setView({ ...view, goal: { ...view.goal, status: action === "pause" ? "paused" : action === "resume" ? "active" : "cleared" } }); setMode(action === "resume" ? "goal" : "execute"); } }} onRefresh={noop} />
      <InputContextProvider sessionId={sessionId} workspaceRoot="/workspace" ready onAdd={noop} onSettings={noop}>
        <Composer composerMode="idle" activeRun={undefined} input={input} workMode={mode} onWorkModeChange={changeMode}
          approvalMode={access} onApprovalModeChange={setAccess} modelList={models} selectedModelId="model" contextUsage={undefined}
          modelConfigured modelLoading={false} isSubmitting={false} submitKind={undefined} cancelingRunId={undefined}
          onInputChange={setInput} onSubmit={noop} onCancel={noop} onModelChange={noop} onOpenModelSettings={noop} />
      </InputContextProvider>
    </div>
  </main>;
}
createRoot(document.getElementById("root")!).render(<Preview />);
