import { createRoot } from "react-dom/client";
import { MemorySettings } from "../src/components/settings/MemorySettings.js";
import type { EidosRuntimeAPI } from "../src/contracts.js";
import "../src/styles.css";

const calls: unknown[] = [];
Object.assign(window, { memoryFixtureCalls: calls });
let failed = false;
Object.assign(window, { failNextMemoryList: () => { failed = true; } });
localStorage.removeItem("eidos.memory.location.v1");
window.eidosRuntime = {
  listProjects: async () => ({ items: [{ id: "eidos", name: "Eidos 的一个名称很长的项目", workspaceRoot: "/Users/example/Documents/Agent Frame/Eidos", gitAvailable: true, createdAt: 1, updatedAt: 1 }] }),
  memory: async (method: string, request: { projectId?: string; scope?: string; path?: string }) => {
    calls.push({ method, request });
    if (method === "memory/list" && failed) { failed = false; throw new Error("fixture offline"); }
    if (method === "memory/read") return {
      path: "MEMORY.md", scopeId: request.projectId ?? "global", revision: 0, currentRevision: 0,
      content: "# Memory\n\n这里还没有主题笔记。", revisions: [], offset: 0, nextOffset: null,
    };
    return { scopes: [
      { id: "global", kind: "global", mode: "auto", privacyEpoch: 0, generation: 0 },
      ...(request.projectId ? [{ id: request.projectId, kind: "project", mode: "read", privacyEpoch: 0, generation: 0 }] : []),
    ], documents: [] };
  },
  exportMemoryNotes: async (request: unknown) => { calls.push({ export: request }); },
} as EidosRuntimeAPI;
createRoot(document.getElementById("root")!).render(<MemorySettings />);
