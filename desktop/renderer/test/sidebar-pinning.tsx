import { createRoot } from "react-dom/client";
import { useState } from "react";
import type { Project, Session } from "../src/contracts.js";
import type { SidebarPinsResponse, SidebarSetPinnedRequest } from "../../shared/sidebar.js";
import { SessionSidebar } from "../src/components/SessionSidebar.js";
import { useSidebarPins } from "../src/app/useSidebarPins.js";
import "../src/styles.css";

declare global {
  interface Window { sidebarTestState: { loaded: boolean; pins: number; selectedId: string }; }
}

const projects: Project[] = Array.from({ length: 15 }, (_, index) => ({
  id: `project-${index}`, name: index === 0 ? "Eidos" : `测试项目 ${index}`,
  workspaceRoot: `/workspace/${index}`, gitAvailable: false, createdAt: 100 - index, updatedAt: 100 - index,
}));
const sessions: Session[] = [
  { id: "22222222-2222-2222-2222-222222222222", title: "排查模型错误", workspaceRoot: "/workspace/0",
    project: projects[0]!, taskStatus: "in_progress", activeRunStatus: "waiting_approval", createdAt: 2, updatedAt: 2 },
  { id: "33333333-3333-3333-3333-333333333333", title: "调整输入框布局", workspaceRoot: "/workspace/0",
    project: projects[0]!, taskStatus: "completed", createdAt: 1, updatedAt: 1 },
];
let saved: SidebarPinsResponse = { items: [], throughEventId: 0 };
window.eidosRuntime = {
  listSidebarPins: async () => saved,
  setSidebarPinned: async ({ target, pinned }: SidebarSetPinnedRequest) => {
    const items = saved.items.filter((item) => item.kind !== target.kind || item.id !== target.id);
    saved = { items: pinned ? [...items, target] : items, throughEventId: saved.throughEventId + 1 };
    return saved;
  },
} as unknown as typeof window.eidosRuntime;

function Fixture() {
  const pins = useSidebarPins(true);
  const [selectedId, setSelectedId] = useState(sessions[0]!.id);
  window.sidebarTestState = { loaded: pins.loaded, pins: pins.pins.length, selectedId };
  return <main className="workbench">
    <SessionSidebar sessions={sessions} projects={projects} selectedId={selectedId} disabled={false}
      pins={pins.pins} pinningDisabled={!pins.loaded} pendingPinTargets={pins.pending}
      onSetPinned={(target, pinned) => void pins.setPinned(target, pinned)}
      readCompletedSessions={new Set()} runtimePresentation={{ tone: "success", label: "Ready" }}
      onCreate={() => {}} onCreateInProject={() => {}} onSelect={(session) => setSelectedId(session.id)}
      onRename={() => {}} onDelete={() => {}} onDeleteProject={() => {}} onOpenSettings={() => {}} />
    <div className="main-pane" style={{ padding: "4rem 2rem" }}>
      <h2>项目和 Session 置顶</h2>
      <p>右键菜单可以置顶和取消置顶。置顶区可以同时显示项目和 Session。</p>
    </div>
  </main>;
}

createRoot(document.getElementById("root")!).render(<Fixture />);
