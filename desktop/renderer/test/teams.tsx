import { createRoot } from "react-dom/client";
import { useState } from "react";
import type { TeamMethods, TeamProposalView, TeamSessionState, TeamView } from "../../shared/teams.js";
import type { ApprovalMode, ModelListResult } from "../src/contracts.js";
import { Composer, type ComposerWorkMode } from "../src/components/Composer.js";
import { InputContextProvider } from "../src/components/InputContext.js";
import { TeamPanel } from "../src/components/teams/TeamPanel.js";
import { TeamRoster } from "../src/components/teams/TeamRoster.js";
import { useTeam } from "../src/components/teams/useTeam.js";
import "../src/styles.css";
import "./teams.css";

const sessionId = "11111111-1111-1111-1111-111111111111";
const models: ModelListResult = { defaultModelId: "model", models: [{ id: "model", name: "DeepSeek-V4.1-Flash", vendor: "DeepSeek", provider: "deepseek",
  url: "https://api.deepseek.com", supportsToolCall: true, supportsImages: false, supportsReasoning: false }] };
const proposal: TeamProposalView = {
  id: "proposal-1", sessionId, runId: "run-1", decision: "pending", createdAt: 1,
  proposal: { objective: "重构登录模块，拆分接口与界面，并补齐失败路径测试", summary: "接口与界面分别实现，测试与核查并行。", template: "feature",
    members: [
      { name: "architect", role: "explorer", workspace: "shared", brief: "梳理登录相关的接口、调用方和风险，给出拆分方案。不修改文件。" },
      { name: "builder", role: "worker", workspace: "worktree", brief: "实现接口拆分，只修改 src/auth 下的文件，并运行相关测试。" },
      { name: "tester", role: "worker", workspace: "worktree", brief: "按验收条件补齐失败路径测试，报告覆盖缺口。" },
      { name: "verifier", role: "verifier", workspace: "shared", brief: "按验收条件和证据审查提交的任务，接受或写明原因退回。" },
    ],
    tasks: [
      { key: "auth.map", title: "梳理调用方", owner: "architect" },
      { key: "auth.api", title: "拆分登录接口", owner: "builder", milestone: "M1" },
      { key: "auth.tests", title: "失败路径测试", owner: "tester", milestone: "M1" },
      { key: "auth.docs", title: "更新说明" },
    ] },
};
const team: TeamView = {
  id: "team-1", leadSessionId: sessionId, status: "active", objective: proposal.proposal.objective, autoWakeLead: true, template: "feature",
  runSettings: { modelId: "model" }, version: 4, createdAt: 1, updatedAt: 4,
  members: [
    { id: "m1", name: "architect", role: "explorer", brief: "梳理接口与风险", modelId: "model", workspaceMode: "shared", status: "active", sessionId: "s1", runId: "r1", runStatus: "succeeded", createdAt: 1 },
    { id: "m2", name: "builder", role: "worker", brief: "实现接口拆分，只修改 src/auth 下的文件，并运行相关测试。", modelId: "model", workspaceMode: "worktree", status: "active", sessionId: "s2", runId: "r2", runStatus: "running", createdAt: 1 },
    { id: "m3", name: "tester", role: "worker", brief: "补齐失败路径测试", modelId: "model", workspaceMode: "worktree", status: "active", sessionId: "s3", runId: "r3", runStatus: "waiting_approval", createdAt: 1 },
    { id: "m4", name: "verifier", role: "verifier", brief: "审查", modelId: "model", workspaceMode: "shared", status: "active", sessionId: "s4", createdAt: 1 },
  ],
  tasks: [
    { id: "t1", key: "auth.map", title: "梳理调用方", status: "done", owner: "architect", version: 3, createdBy: "lead", updatedAt: 2 },
    { id: "t2", key: "auth.api", title: "拆分登录接口", status: "in_review", owner: "builder", reviewer: "verifier", milestone: "M1", reviewRequired: true,
      evidence: [{ toolCallId: "call-1" }, { toolCallId: "call-2" }], deliveryStatus: "ready", note: "接口已拆分，相关测试通过。", version: 4, createdBy: "lead", updatedAt: 4 },
    { id: "t3", key: "auth.tests", title: "失败路径测试", status: "in_progress", owner: "tester", milestone: "M1", version: 2, createdBy: "lead", updatedAt: 3 },
    { id: "t4", key: "gate.1.verifier", kind: "gate", title: "Gate: M1", status: "open", owner: "verifier", blocked: true, milestone: "M1", version: 1, createdBy: "system", updatedAt: 2 },
    { id: "t5", key: "auth.docs", title: "更新说明", status: "open", dependsOn: ["gate.1.verifier"], blocked: true, version: 1, createdBy: "lead", updatedAt: 2 },
  ],
};
const messages = [
  { id: "a", seq: 1, channel: "board", author: "system", authorKind: "system", recipients: ["architect"], content: "You joined the team. Read your brief, the task board and the roster, then start.", createdAt: Date.now() - 600000 },
  { id: "b", seq: 2, channel: "board", author: "architect", authorKind: "member", toAll: true, content: "调用方共 7 处，集中在 src/auth 与 src/session。拆分方案已写入任务说明。", task: "auth.map", createdAt: Date.now() - 420000 },
  { id: "c", seq: 3, channel: "direct", author: "builder", authorKind: "member", recipients: ["lead"], content: "接口拆分完成，等待 verifier 审查。", task: "auth.api", createdAt: Date.now() - 60000 },
  { id: "d", seq: 4, channel: "board", author: "user", authorKind: "user", recipients: ["tester"], content: "测试里也覆盖一下令牌过期的情况。", createdAt: Date.now() - 30000 },
];
const noop = () => {};
type View = "entry" | "roster" | "team" | "team-wide";
declare global { interface Window { teamTest: { show: (value: View) => void } } }
const state: { value: TeamSessionState } = { value: { team, usage: [
  { member: "builder", inputTokens: 18200, outputTokens: 4100 }, { member: "tester", inputTokens: 9100, outputTokens: 2200, incomplete: true },
] } };
window.eidosRuntime = {
  listSkills: async () => ({ skills: [] }), listPlugins: async () => ({ plugins: [] }), listMcpServers: async () => ({ servers: [] }),
  listSessions: async () => ({ items: [] }), listAgentTypes: async () => ({ agents: [] }),
  listWorkspaceDirectory: async () => ({ path: ".", entries: [], truncated: false }), onInputQuote: () => noop,
  onNotification: () => noop, listModels: async () => models,
  readSession: async () => ({ session: { id: "s2", title: "builder" }, items: [], runs: [] }),
  team: async (method: keyof TeamMethods) => method === "team/read" ? state.value
    : method === "team/messages" ? { messages, nextCursor: 4, hasMore: false }
    : method === "team/memberChanges" ? { member: "builder", status: "ready", files: [] } : team,
} as unknown as typeof window.eidosRuntime;

function Dock() {
  const value = useTeam(sessionId, true);
  const [member, setMember] = useState<string>();
  if (!value.team) return null;
  return <TeamPanel team={value.team} usage={value.usage} actions={value} memberName={member} onOpenMember={setMember}
    onBack={() => setMember(undefined)} onCancelRun={noop} approvals={[]} onApprove={noop} onReject={noop} models={models.models} />;
}

function Preview() {
  const [view, setView] = useState<View>("entry");
  const [mode, setMode] = useState<ComposerWorkMode>("execute");
  const [input, setInput] = useState("重构登录模块，拆分接口与界面，并补齐失败路径测试");
  const [access, setAccess] = useState<ApprovalMode>("manual");
  window.teamTest = { show: setView };
  return <main className={`team-preview${view === "team-wide" ? " team-preview--wide" : ""}`}>
    {view !== "team-wide" && (
      <section className="team-preview__session">
        <article><p>先梳理登录相关的调用方，再按模块组建团队。</p></article>
        {view === "roster"
          ? <TeamRoster proposal={proposal} approvalMode={access === "full_access" ? "full_access" : access} modelLabel="DeepSeek-V4.1-Flash"
              worktreeAvailable busy={false} error={undefined} onDecide={() => setView("team")} />
          : <InputContextProvider sessionId={sessionId} workspaceRoot="/workspace" ready onAdd={noop} onSettings={noop}>
              <Composer composerMode="idle" activeRun={undefined} input={input} workMode={mode} onWorkModeChange={setMode}
                teamAvailable={view === "entry"} approvalMode={access} onApprovalModeChange={setAccess} modelList={models} selectedModelId="model"
                contextUsage={undefined} modelConfigured modelLoading={false} isSubmitting={false} submitKind={undefined}
                cancelingRunId={undefined} onInputChange={setInput} onSubmit={noop} onCancel={noop} onModelChange={noop} onOpenModelSettings={noop} />
            </InputContextProvider>}
      </section>
    )}
    {(view === "team" || view === "team-wide") && <aside className="team-preview__dock"><Dock /></aside>}
  </main>;
}
createRoot(document.getElementById("root")!).render(<Preview />);
