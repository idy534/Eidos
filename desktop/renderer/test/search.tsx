import { createRoot } from "react-dom/client";
import { AppShell } from "../src/app/AppShell.js";
import { deriveRuntimePresentation } from "../src/session-state.js";
import { createWelcomeRuntime } from "./welcome-runtime.js";
import { ArtifactProvider } from "../src/components/ArtifactContext.js";
import { ExecutionFeed } from "../src/components/ExecutionFeed.js";
import { TurnResults } from "../src/components/TurnResults.js";
import { InputReferenceCards } from "../src/components/InputContext.js";
import type { InputReference } from "../../shared/input-context.js";
import type { Item, Run, Session, SessionSnapshot } from "../src/contracts.js";
import "../src/styles.css";

const project = { id: "11111111-1111-4111-8111-111111111111", name: "Eidos", workspaceRoot: "/isolated/search", gitAvailable: false, createdAt: 1, updatedAt: 1 };
const session: Session = { id: "22222222-2222-4222-8222-222222222222", title: "搜索与历史定位", project, workspaceRoot: project.workspaceRoot, taskStatus: "completed", createdAt: Date.now() - 3_600_000, updatedAt: Date.now() - 1_800_000 };
const recentSessions: Session[] = [session, ...Array.from({ length: 11 }, (_, index) => ({ ...session, id: `recent-${index}`, title: `最近会话 ${index + 1}`, updatedAt: session.updatedAt - (index + 1) * 3_600_000 }))];
const run: Run = { id: "33333333-3333-4333-8333-333333333333", sessionId: session.id, modelId: "welcome-model", status: "succeeded", modelStepCount: 1, createdAt: 1, updatedAt: 2, allowedActions: [] };
const item: Item = { id: "44444444-4444-4444-8444-444444444444", sessionId: session.id, runId: run.id, ordinal: 2, kind: "assistant_message", status: "completed", createdAt: 2,
  content: "中文线程与 needle，第二个 needle\n\n你可以打开 [sample.ts](/isolated/search/sample.ts) 查看文件正文\n\n" + "这是用于检查搜索滚动位置的正文\n\n".repeat(30) };
const old: Item = { ...item, id: "55555555-5555-4555-8555-555555555555", ordinal: 1, content: "早期记录 old-anchor" };
const snapshot: SessionSnapshot = { session, runs: [run], items: [item], stepResolutions: [], throughEventId: 0 };
const content = "// 中文线程 needle\nconst first = 'needle';\nconst second = '中文';\n";
const testState = { globalCalls: 0, pending: false };
Object.assign(window, { searchFixture: testState });
window.eidosRuntime = { ...createWelcomeRuntime(),
  readInputDraft: async () => ({ text: "", references: [] }),
  writeInputDraft: async (_key, draft) => draft,
  listProjects: async () => ({ items: [project] }), listSessions: async () => ({ items: recentSessions.toReversed() }),
  readSession: async () => snapshot,
  readSessionAround: async () => ({ ...snapshot, items: [old] }),
  searchSessions: async (request) => {
    if (!request.sessionId) {
      testState.globalCalls += 1;
      if (testState.globalCalls === 2) { testState.pending = true; await new Promise((resolve) => setTimeout(resolve, 650)); testState.pending = false; }
    }
    return { indexing: !request.sessionId && testState.globalCalls === 1, reset: false, projects: [], items: request.query ? [{ sessionId: session.id, title: session.title!, projectName: "Eidos", snippet: request.query === "old" ? old.content! : item.content!.slice(0, 100), itemId: request.query === "old" ? old.id : item.id, occurrence: 0, matchKind: "message" }] : [] };
  },
  listWorkspaceDirectory: async () => ({ path: ".", entries: [{ name: "sample.ts", relativePath: "sample.ts", kind: "file", sizeBytes: content.length }], truncated: false }),
  readWorkspaceFilePreview: async () => ({ path: "sample.ts", kind: "code", content, version: "a".repeat(64), truncated: false, sizeBytes: content.length, language: "typescript" }),
} as typeof window.eidosRuntime;
window.localStorage.setItem("eidos.sidebarOpen", "true");
const status = { state: "ready", protocolVersion: 1, runtimeVersion: "test", runShell: true, modelConfigured: true, storageHealth: { state: "ready" } } as const;
const imageSurface = new URLSearchParams(location.search).get("image-close");
const noop = () => {};
function ImageCloseFixture({ surface }: { surface: string }) {
  const imageUrl = "data:image/svg+xml," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="240" height="160"><rect width="240" height="160" fill="#688776"/></svg>');
  const reference: InputReference = { id: "image-ref", kind: "image", source: "sample.png", label: "sample.png", sha256: "a".repeat(64), status: "content", size: 20 };
  Object.assign(window.eidosRuntime, {
    prepareWorkspacePreview: async () => imageUrl, releaseWorkspacePreview: async () => {},
    readWorkspaceFilePreview: async () => ({ path: "sample.png", kind: "image", sizeBytes: 20, version: "a".repeat(64), truncated: false }),
    readInput: async () => ({ reference, text: "", thumbnail: imageUrl }),
    prepareInputPreview: async () => imageUrl, releaseInputPreview: async () => {},
  });
  const tool: Item = { ...item, id: "image-tool-item", ordinal: 1, kind: "tool_call", content: "", toolCall: {
    id: "image-tool", itemId: "image-tool-item", modelStepIndex: 1, batchOrder: 0, providerCallId: "image-call", toolName: "view_image", status: "completed", startedAt: 1, completedAt: 2,
    argumentsJson: JSON.stringify({ path: "sample.png" }),
    resultJson: JSON.stringify({ outcome: "success", code: "ok", data: { path: "sample.png", mime: "image/png", size: 20 } }),
  } };
  const output: Item = { ...tool, toolCall: { ...tool.toolCall!, toolName: "declare_outputs",
    provenance: { kind: "builtin", sourceId: "eidos.declare-outputs", sourceVersion: "1", contentHash: "a".repeat(64) },
    resultJson: JSON.stringify({ outcome: "success", code: "ok", data: { executionRoot: project.workspaceRoot, outputs: [{ path: "sample.png", sizeBytes: 20, version: "a".repeat(64) }] } }),
  } };
  return <ArtifactProvider value={{ sessionId: session.id, executionRoot: project.workspaceRoot, openFile: noop, openBrowser: noop }}>
    <div style={{ height: "100vh", display: "flex", flexDirection: "column", padding: "1rem" }}>
      {surface === "reference" ? <InputReferenceCards references={[reference]} /> : surface === "output" ? <TurnResults run={run} items={[output]} />
        : <ExecutionFeed items={[tool]} runs={[run]} approvals={[]} onApprove={noop} onReject={noop} />}
    </div>
  </ArtifactProvider>;
}
createRoot(document.getElementById("root")!).render(imageSurface ? <ImageCloseFixture surface={imageSurface} />
  : <AppShell runtime={{ status, presentation: deriveRuntimePresentation(status), isStorageReady: true, restarting: false, restartRuntime: async () => {} }} />);
