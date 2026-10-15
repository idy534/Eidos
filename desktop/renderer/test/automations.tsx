import { createRoot } from "react-dom/client";
import type { Automation, AutomationRequest, AutomationResponse } from "../../shared/automation.js";
import type { ModelOption, Project, Session } from "../src/contracts.js";
import { AutomationPage } from "../src/components/automations/AutomationPage.js";
import "../src/styles.css";

declare global { interface Window { automationTestState: { commands: AutomationRequest[] }; } }
window.automationTestState = { commands: [] };
const project = { id: "project_eidos", name: "Eidos", workspaceRoot: "/workspace/eidos", gitAvailable: true, createdAt: 1, updatedAt: 1 } as Project;
const session = { id: "22222222-2222-2222-2222-222222222222", title: "部署检查", project, taskStatus: "completed", workspaceRoot: project.workspaceRoot, createdAt: 1, updatedAt: 1 } as Session;
const model = { id: "model", name: "本地配置模型" } as ModelOption;
let task: Automation = { id: "11111111-1111-1111-1111-111111111111", revision: 1, name: "检查部署", prompt: "检查项目部署状态。部署成功后报告结果，并暂停本定时任务。",
  kind: "cron", schedule: { expression: "*/10 * * * *", timezone: "Asia/Shanghai" }, status: "ACTIVE", model: { modelId: model.id }, approvalMode: "auto_review",
  target: { kind: "existing_session", sessionId: session.id, projectId: project.id, execution: { executionMode: "local" } }, createdAt: 1, updatedAt: 1 };
window.eidosRuntime = {
  automation: async ({ command }) => {
    window.automationTestState.commands.push(command);
    if (command.mode === "preview") return { kind: "preview", nextRuns: ["2026-10-16T01:00:00Z"] };
    if (command.mode === "create") task = { ...task, name: command.name!, prompt: command.prompt!, approvalMode: command.approvalMode!, target: { kind: "new_session", projectId: command.projectId ?? null } };
    return command.mode === "view" && !command.id ? { kind: "list", items: [task] } : { kind: "detail", automation: task };
  },
  automationHistory: async () => ({ kind: "history", occurrences: [] }),
  automationRunNow: async () => ({ kind: "run" }), onNotification: () => () => {},
} as unknown as typeof window.eidosRuntime;
createRoot(document.getElementById("root")!).render(<main className="workbench workbench--sidebar-collapsed"><aside className="sidebar" inert /><section className="workspace"><AutomationPage projects={[project]} sessions={[session]} models={[model]} currentSessionId={session.id} onClose={() => {}} onOpenSession={() => {}} /></section></main>);
