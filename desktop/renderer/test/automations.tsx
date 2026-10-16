import { createRoot } from "react-dom/client";
import { useState } from "react";
import type { Automation, AutomationDesktopRequest, AutomationExecuteRequest } from "../../shared/automation.js";
import type { ModelOption, Project, Session } from "../src/contracts.js";
import { AutomationPage } from "../src/components/automations/AutomationPage.js";
import { SessionSidebar } from "../src/components/SessionSidebar.js";
import "../src/styles.css";

declare global { interface Window { automationTestState: { commands: AutomationDesktopRequest[]; requests: AutomationExecuteRequest[] }; } }
window.automationTestState = { commands: [], requests: [] };
const project = { id: "project_eidos", name: "Eidos", workspaceRoot: "/workspace/eidos", gitAvailable: true, createdAt: 1, updatedAt: 1 } as Project;
const session = { id: "22222222-2222-2222-2222-222222222222", title: "部署检查", project, taskStatus: "completed", workspaceRoot: project.workspaceRoot, createdAt: 1, updatedAt: 1 } as Session;
const model = { id: "model", name: "本地配置模型", provider: "deepseek", supportsReasoning: true, reasoning: { defaultSelection: "high", selections: ["none", "high"] } } as ModelOption;
let task: Automation = { id: "11111111-1111-1111-1111-111111111111", revision: 1, name: "检查部署", prompt: "检查项目部署状态。部署成功后报告结果，并暂停本定时任务。",
  kind: "cron", schedule: { expression: "*/10 * * * *", timezone: "Asia/Shanghai" }, status: "ACTIVE", model: { modelId: model.id }, approvalMode: "auto_review",
  target: { kind: "existing_session", sessionId: session.id, projectId: project.id, execution: { executionMode: "local" } }, createdAt: 1, updatedAt: 1 };
const waiting: Automation = { ...task, id: "33333333-3333-3333-3333-333333333333", name: "稍后提醒", kind: "at", schedule: { runAt: "2026-10-17T09:00:00+08:00", timezone: "Asia/Shanghai" } };
const paused: Automation = { ...task, id: "44444444-4444-4444-4444-444444444444", name: "暂停检查", status: "PAUSED" };
const fired: Automation = { ...waiting, id: "55555555-5555-5555-5555-555555555555", name: "一次性提醒", status: "PAUSED", pauseReason: "one_shot_fired" };
window.eidosRuntime = {
  automation: async (request: AutomationExecuteRequest) => {
    const { command } = request;
    window.automationTestState.commands.push(command);
    window.automationTestState.requests.push(request);
    if (command.mode === "preview") return { kind: "preview", nextRuns: ["2026-10-16T01:00:00Z"] };
    if (command.mode === "create") task = { ...task, name: command.name!, prompt: command.prompt!, approvalMode: command.approvalMode!,
      fullAccessConfirmation: request.fullAccessConfirmation ?? null, target: { kind: "new_session", projectId: command.projectId ?? null } };
    const tasks = [task, waiting, paused, fired];
    return command.mode === "view" && !command.id ? { kind: "list", items: tasks.map((item) => ({ ...item,
      nextRunAt: item.status === "ACTIVE" ? item.schedule.runAt ?? "2026-10-17T01:10:00Z" : null })) }
      : { kind: "detail", automation: tasks.find((item) => item.id === command.id) ?? task };
  },
  automationHistory: async () => ({ kind: "history", occurrences: [] }),
  automationRunNow: async () => ({ kind: "run" }), onNotification: () => () => {},
} as unknown as typeof window.eidosRuntime;
function SourceFixture() {
  const [detailId, setDetailId] = useState<string>();
  const generated: Session = { ...session, id: "66666666-6666-6666-6666-666666666666", title: task.name,
    automationOrigin: { automationId: task.id, revision: 1, occurrenceId: "77777777-7777-7777-7777-777777777777", scheduledFor: "manual:hidden-source" } };
  return <main className={`workbench${detailId ? " workbench--sidebar-collapsed workbench--settings workbench--automations" : ""}`}>
    <SessionSidebar collapsed={Boolean(detailId)} sessions={[generated]} projects={[project]} selectedId={generated.id}
      disabled={false} readCompletedSessions={new Set()} runtimePresentation={{ tone: "success", label: "Ready" }}
      onCreate={() => {}} onCreateInProject={() => {}} onSelect={() => {}} onRename={() => {}} onDelete={() => {}}
      onDeleteProject={() => {}} onOpenSettings={() => {}} onOpenAutomation={setDetailId} />
    <section className="workspace">{detailId && <AutomationPage projects={[project]} sessions={[generated]} models={[model]}
      initialAutomationId={detailId} runtimeVersion="0.5.4" onClose={() => setDetailId(undefined)} onOpenSession={() => {}} />}</section>
  </main>;
}

createRoot(document.getElementById("root")!).render(window.location.search.includes("source") ? <SourceFixture /> :
  <main className="workbench workbench--sidebar-collapsed workbench--settings workbench--automations"><aside className="sidebar" inert /><section className="workspace"><AutomationPage projects={[project]} sessions={[session]} models={[model]} currentSessionId={session.id} runtimeVersion="0.5.4" onClose={() => {}} onOpenSession={() => {}} /></section></main>);
