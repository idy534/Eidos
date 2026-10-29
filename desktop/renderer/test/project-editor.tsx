import { useState } from "react";
import { createRoot } from "react-dom/client";
import { ProjectEditorDialog } from "../src/components/ProjectEditorDialog.js";
import { ProjectMutationRetry } from "../src/app/project-mutation-retry.js";
import type { EidosRuntimeAPI, Project } from "../src/contracts.js";
import type { ProjectRootInput } from "../../shared/project-workspaces.generated.js";
import "../src/styles.css";

const fixture = { failSave: false, saves: [] as unknown[], folder: "/Users/example/Projects/extra", delay: 250 };
Object.assign(window, { projectEditorFixture: fixture });
window.eidosRuntime = { selectWorkspace: async () => fixture.folder } as unknown as EidosRuntimeAPI;
const project: Project = { id: "multi", name: "agentic-kit", revision: 1, primaryRootId: "kit", workspaceRoot: "/Users/example/Projects/agentic-kit", gitAvailable: true,
  roots: [
    { id: "kit", path: "/Users/example/Projects/agentic-kit", repositoryRoot: "/Users/example/Projects/agentic-kit" },
    { id: "service", path: "/Users/example/Projects/appforge-service", repositoryRoot: "/Users/example/Projects/appforge-service" },
    { id: "rag", path: "/Users/example/Documents/Notes/rag-engine", repositoryRoot: null },
  ], createdAt: 1, updatedAt: 1 };
const retry = new ProjectMutationRetry();
function Fixture() {
  const [open, setOpen] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  async function save(name: string | undefined, roots: ProjectRootInput[], primaryRootId: string) {
    const first = roots[0]; if (!first) return;
    fixture.saves.push(retry.prepare({ projectId: project.id, expectedRevision: 1, name, roots: [first, ...roots.slice(1)], primaryRootId }));
    setBusy(true); setError(undefined);
    await new Promise(resolve => setTimeout(resolve, fixture.delay));
    setBusy(false);
    if (fixture.failSave) { setError("项目保存失败。你的修改已保留，请重试。"); return; }
    setOpen(false);
  }
  return <div style={{ minHeight: "100vh", background: "var(--surface)" }}>
    <button onClick={() => { retry.reset(); setOpen(true); }}>编辑项目</button>
    <ProjectEditorDialog open={open} project={project} busy={busy} error={error}
      onSave={(name, roots, primary) => void save(name, roots, primary)} onCancel={() => setOpen(false)}
      onRemove={() => {}} canRemove={false} />
  </div>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
