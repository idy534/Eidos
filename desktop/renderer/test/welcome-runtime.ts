import type { EidosRuntimeAPI, ModelListResult, Run, Session, SessionSnapshot } from "../src/contracts.js";

/** Isolated in-memory IPC fixture. It never contacts a Provider or the filesystem. */
export function createWelcomeRuntime() {
  let sessions: Session[] = [];
  const runs: Run[] = [];
  const models: ModelListResult = {
    defaultModelId: "welcome-model",
    models: [{ id: "welcome-model", name: "Eidos Test Model", vendor: "OpenAI", provider: "openai",
      url: "https://example.com/v1", supportsToolCall: true, supportsImages: true, supportsReasoning: false }],
  };
  const api: Partial<EidosRuntimeAPI> = {
    onNotification: () => () => {},
    onApprovalRequest: () => () => {},
    onShortcut: () => () => {},
    listProjects: async () => ({ items: [] }),
    listSessions: async () => ({ items: sessions }),
    listSidebarPins: async () => ({ items: [], throughEventId: 0 }),
    listModels: async () => models,
    readContextUsage: async () => null,
    listPendingApprovals: async () => [],
    readResponseActionState: async () => ({ feedback: [], revisions: [] }),
    listQueuedRuns: async () => ({ items: [] }),
    listEvents: async () => ({ items: [], throughEventId: 0, hasMore: false }),
    createSession: async () => {
      const session: Session = { id: crypto.randomUUID(), title: "新会话", projectless: true,
        workspaceRoot: "/isolated/welcome", executionMode: "local", taskStatus: "new", createdAt: 1, updatedAt: 1 };
      sessions = [session, ...sessions];
      return session;
    },
    readSession: async (id): Promise<SessionSnapshot> => ({
      session: sessions.find((session) => session.id === id)!,
      runs: runs.filter((run) => run.sessionId === id), items: [], stepResolutions: [],
    }),
    deleteSession: async (id) => {
      sessions = sessions.filter((session) => session.id !== id);
      return { deletedSessionId: id };
    },
    startRun: async (sessionId, _input, modelId, _reasoning, approvalMode) => {
      const run: Run = { id: crypto.randomUUID(), sessionId, modelId, status: "running",
        approvalMode: approvalMode ?? "manual", modelStepCount: 0, createdAt: 1, updatedAt: 1, allowedActions: ["cancel"] };
      runs.push(run);
      return run;
    },
  };
  return api as EidosRuntimeAPI;
}
