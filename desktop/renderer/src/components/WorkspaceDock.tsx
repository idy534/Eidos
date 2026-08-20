import type { ReactNode } from "react";
import { DropdownMenu } from "./DropdownMenu.js";
import "./WorkspaceDock.css";

export type WorkspaceToolKind = "review" | "terminal" | "files";

interface WorkspaceDockProps {
  activeTool: WorkspaceToolKind;
  availableTools: WorkspaceToolKind[];
  expanded: boolean;
  openTools: WorkspaceToolKind[];
  filesTitle?: string | undefined;
  terminalTitle?: string | undefined;
  onAddTool: (tool: WorkspaceToolKind) => void;
  onClose: () => void;
  onCloseTool: (tool: WorkspaceToolKind) => void;
  onSelectTool: (tool: WorkspaceToolKind) => void;
  onToggleExpanded: () => void;
  review: ReactNode;
  terminal: ReactNode;
  files: ReactNode;
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
        <path d="M2.5 6.5h5l1.5-2h3l1.5 2h4v9.5H2.5z" />
        <path d="M4.5 4h4" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <rect x="3" y="2.5" width="14" height="15" rx="3" />
      <path d="M7 7h6M7 10h6M7 13h4M10 5v4M8 7h4" />
    </svg>
  );
}

export function WorkspaceDock({
  activeTool,
  availableTools,
  expanded,
  openTools,
  filesTitle,
  terminalTitle,
  onAddTool,
  onClose,
  onCloseTool,
  onSelectTool,
  onToggleExpanded,
  review,
  terminal,
  files,
}: WorkspaceDockProps) {
  const labels: Record<WorkspaceToolKind, string> = {
    ...TOOL_LABELS,
    files: filesTitle || TOOL_LABELS.files,
    terminal: terminalTitle || TOOL_LABELS.terminal,
  };
  const content: Record<WorkspaceToolKind, ReactNode> = { review, terminal, files };
  const addableTools = availableTools.filter((tool) => !openTools.includes(tool));

  return (
    <aside
      id="workspace-dock"
      className={`workspace-dock${expanded ? " workspace-dock--expanded" : ""}`}
      aria-label="工作区工具"
    >
      <header className="workspace-dock__header">
        <div className="workspace-dock__tabs" role="tablist" aria-label="工作区窗口">
          {openTools.map((tool) => (
            <div className="workspace-dock__tab" key={tool}>
              <button
                id={`workspace-tool-tab-${tool}`}
                type="button"
                role="tab"
                aria-controls={`workspace-tool-panel-${tool}`}
                aria-selected={activeTool === tool}
                onClick={() => onSelectTool(tool)}
              >
                <span className="workspace-dock__tool-icon"><ToolIcon tool={tool} /></span>
                <span>{labels[tool]}</span>
              </button>
              <button
                className="workspace-dock__tab-close"
                type="button"
                aria-label={`关闭${TOOL_LABELS[tool]}`}
                onClick={() => onCloseTool(tool)}
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
            <svg viewBox="0 0 20 20" aria-hidden="true">
              {expanded
                ? <path d="M8 3v5H3M12 17v-5h5M8 8 3 3M12 12l5 5" />
                : <path d="M8 8H3V3M12 12h5v5M8 8 3 3M12 12l5 5" />}
            </svg>
          </button>
          <button type="button" className="icon-button" aria-label="关闭工作区工具" onClick={onClose}>
            <svg viewBox="0 0 20 20" aria-hidden="true">
              <path d="M3 3.5h14v13H3zM12.5 3.5v13" />
            </svg>
          </button>
        </div>
      </header>

      <div className="workspace-dock__body">
        {openTools.map((tool) => (
          <section
            id={`workspace-tool-panel-${tool}`}
            key={tool}
            role="tabpanel"
            aria-labelledby={`workspace-tool-tab-${tool}`}
            hidden={activeTool !== tool}
          >
            {content[tool]}
          </section>
        ))}
        {openTools.length === 0 && (
          <div className="workspace-dock__empty" role="status">
            <p>没有打开的窗口</p>
            <span>使用“＋”添加审阅、终端或文件。</span>
          </div>
        )}
      </div>
    </aside>
  );
}
