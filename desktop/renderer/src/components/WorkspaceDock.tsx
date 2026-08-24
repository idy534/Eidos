import type { ReactNode } from "react";
import { DropdownMenu } from "./DropdownMenu.js";
import "./WorkspaceDock.css";

export type WorkspaceToolKind = "review" | "terminal" | "files";

export interface WorkspaceTab {
  id: string;
  kind: WorkspaceToolKind;
  title?: string;
}

interface WorkspaceDockProps {
  actions?: ReactNode;
  activeTabId: string | undefined;
  availableTools: WorkspaceToolKind[];
  expanded: boolean;
  openTabs: WorkspaceTab[];
  onAddTool: (tool: WorkspaceToolKind) => void;
  onCloseTab: (tabId: string) => void;
  onSelectTab: (tabId: string) => void;
  onToggleExpanded: () => void;
  renderTab: (tab: WorkspaceTab) => ReactNode;
}

const TOOL_LABELS: Record<WorkspaceToolKind, string> = {
  review: "审阅",
  terminal: "终端",
  files: "文件",
};

function ToolIcon({ tool }: { tool: WorkspaceToolKind }) {
  if (tool === "terminal") {
    return (
      <svg viewBox="0 0 20 20" aria-hidden="true">
        <rect x="2.5" y="3.5" width="15" height="13" rx="3" />
        <path d="m6 8 2 2-2 2M10.5 12h3" />
      </svg>
    );
  }
  if (tool === "files") {
    return (
      <svg viewBox="0 0 20 20" aria-hidden="true">
        <path d="M2.5 5h5l1.5 2h8.5v9.5h-15zM2.5 7h15" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M5 2.5h7l3 3v12H5zM12 2.5v3h3M7.5 10h4M9.5 8v4M7.5 14h4" />
    </svg>
  );
}

function tabLabel(tab: WorkspaceTab): string {
  return tab.title || TOOL_LABELS[tab.kind];
}

export function WorkspaceDockToggle({
  open,
  onClick,
}: {
  open: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className="workspace-dock-toggle"
      aria-label={open ? "关闭工作区工具" : "打开工作区工具"}
      aria-expanded={open}
      aria-controls="workspace-dock"
      onClick={onClick}
    >
      <svg viewBox="0 0 20 20" aria-hidden="true" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3.5" width="14" height="13" rx="3" />
        <path d="M12.5 3.5v13" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </button>
  );
}

export function WorkspaceDock({
  actions,
  activeTabId,
  availableTools,
  expanded,
  openTabs,
  onAddTool,
  onCloseTab,
  onSelectTab,
  onToggleExpanded,
  renderTab,
}: WorkspaceDockProps) {
  const addableTools = availableTools.filter((tool) => (
    tool === "terminal" || !openTabs.some((tab) => tab.kind === tool)
  ));

  return (
    <aside
      id="workspace-dock"
      className={`workspace-dock${expanded ? " workspace-dock--expanded" : ""}`}
      aria-label="工作区工具"
    >
      <header className="workspace-dock__header">
        <div className="workspace-dock__tabs" role="tablist" aria-label="工作区窗口">
          {openTabs.map((tab) => (
            <div className="workspace-dock__tab" key={tab.id}>
              <button
                id={`workspace-tool-tab-${tab.id}`}
                type="button"
                role="tab"
                aria-controls={`workspace-tool-panel-${tab.id}`}
                aria-selected={activeTabId === tab.id}
                onClick={() => onSelectTab(tab.id)}
              >
                <span className="workspace-dock__tool-icon"><ToolIcon tool={tab.kind} /></span>
                <span>{tabLabel(tab)}</span>
              </button>
              <button
                className="workspace-dock__tab-close"
                type="button"
                aria-label={`关闭${tabLabel(tab)}`}
                onClick={() => onCloseTab(tab.id)}
              >
                ×
              </button>
            </div>
          ))}
        </div>

        <DropdownMenu
          className="workspace-dock__add"
          trigger={<><span aria-hidden="true">＋</span><span className="sr-only">添加窗口</span></>}
          label="添加窗口"
          items={addableTools.length > 0
            ? addableTools.map((tool) => ({
                key: tool,
                label: TOOL_LABELS[tool],
                onClick: () => onAddTool(tool),
              }))
            : [{ key: "none", label: "全部窗口已打开", disabled: true, onClick: () => undefined }]}
        />

        <div className="workspace-dock__actions">
          <button
            type="button"
            className="icon-button"
            aria-label={expanded ? "收缩工作区" : "展开工作区"}
            aria-pressed={expanded}
            onClick={onToggleExpanded}
          >
            <svg viewBox="0 0 24 24" data-icon={expanded ? "exit-fullscreen" : "fullscreen"} aria-hidden="true">
              {expanded
                ? <path d="M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7" />
                : <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />}
            </svg>
          </button>
          {actions}
        </div>
      </header>

      <div className="workspace-dock__body">
        {openTabs.map((tab) => (
          <section
            id={`workspace-tool-panel-${tab.id}`}
            key={tab.id}
            role="tabpanel"
            aria-labelledby={`workspace-tool-tab-${tab.id}`}
            hidden={activeTabId !== tab.id}
          >
            {renderTab(tab)}
          </section>
        ))}
        {openTabs.length === 0 && (
          <div className="workspace-dock__empty" role="status">
            <p>打开工作区</p>
            <div className="workspace-dock__empty-list">
              {availableTools.map((tool) => (
                <button
                  type="button"
                  className="workspace-dock__empty-option"
                  key={tool}
                  onClick={() => onAddTool(tool)}
                >
                  <span className="workspace-dock__tool-icon"><ToolIcon tool={tool} /></span>
                  <span>{TOOL_LABELS[tool]}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}
