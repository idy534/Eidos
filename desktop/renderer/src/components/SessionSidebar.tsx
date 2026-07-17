import { useState } from "react";

import type { ModelId, Session } from "../contracts";
import { groupSessionsByWorkspace, taskStatusPresentation } from "../session-state";
import { EidosMark } from "./EidosMark";


interface Props {
  sessions: Session[];
  selectedId: string | undefined;
  disabled: boolean;
  modelId: ModelId;
  modelConfigured: boolean;
  readCompletedSessions: ReadonlySet<string>;
  onCreate: () => void;
  onCreateInWorkspace: (workspaceRoot: string) => void;
  onSelect: (session: Session) => void;
  onRename: (session: Session) => void;
  onDelete: (session: Session) => void;
  onOpenSettings: () => void;
}

export function SessionSidebar({
  sessions, selectedId, disabled, modelId, modelConfigured, readCompletedSessions,
  onCreate, onCreateInWorkspace, onSelect, onRename, onDelete, onOpenSettings,
}: Props) {
  const workspaces = groupSessionsByWorkspace(sessions);
  const [collapsedWorkspaces, setCollapsedWorkspaces] = useState<Set<string>>(new Set());
  const [contextSessionId, setContextSessionId] = useState<string>();
  return (
    <aside className="sidebar" aria-label="任务导航">
      <div className="brand-row">
        <span className="brand-mark" aria-hidden="true">
          <EidosMark />
        </span>
        <span>Eidos</span>
      </div>
      <button className="new-session" disabled={disabled} onClick={onCreate}>＋ 新建任务</button>
      <nav aria-label="工作空间与任务">
        <p className="nav-label">项目</p>
        {sessions.length === 0 ? (
          <p className="nav-empty">还没有任务</p>
        ) : (
          <ul className="workspace-list">
            {workspaces.map((workspace) => (
              <li key={workspace.workspaceRoot}>
                <section className="workspace-group" aria-label={basename(workspace.workspaceRoot)}>
                  <div className="workspace-title-row" title={workspace.workspaceRoot}>
                    <button
                      className="workspace-toggle"
                      aria-expanded={!collapsedWorkspaces.has(workspace.workspaceRoot)}
                      onClick={() => setCollapsedWorkspaces((current) => {
                        const next = new Set(current);
                        if (next.has(workspace.workspaceRoot)) {
                          next.delete(workspace.workspaceRoot);
                        } else {
                          next.add(workspace.workspaceRoot);
                        }
                        return next;
                      })}
                    >
                      <FolderIcon open={!collapsedWorkspaces.has(workspace.workspaceRoot)} />
                      <span>{basename(workspace.workspaceRoot)}</span>
                    </button>
                    <button
                      className="workspace-add"
                      aria-label={`在 ${basename(workspace.workspaceRoot)} 中新建任务`}
                      disabled={disabled}
                      onClick={() => {
                        setCollapsedWorkspaces((current) => {
                          const next = new Set(current);
                          next.delete(workspace.workspaceRoot);
                          return next;
                        });
                        onCreateInWorkspace(workspace.workspaceRoot);
                      }}
                    >＋</button>
                  </div>
                  {!collapsedWorkspaces.has(workspace.workspaceRoot) && <ul className="session-list">
                    {workspace.sessions.map((session) => {
                      const status = taskStatusPresentation(
                        session.taskStatus,
                        readCompletedSessions.has(session.id),
                      );
                      return <li className="session-item" key={session.id}>
                        <button
                          className={session.id === selectedId ? "selected" : ""}
                          aria-current={session.id === selectedId ? "page" : undefined}
                          aria-haspopup="menu"
                          disabled={disabled}
                          onClick={() => {
                            setContextSessionId(undefined);
                            onSelect(session);
                          }}
                          onContextMenu={(event) => {
                            event.preventDefault();
                            setContextSessionId(session.id);
                          }}
                          onKeyDown={(event) => {
                            if (event.shiftKey && event.key === "F10") {
                              event.preventDefault();
                              setContextSessionId(session.id);
                            }
                          }}
                        >
                          <span className="session-title">{session.title ?? "新任务"}</span>
                          {status && (
                            <span
                              className={`task-indicator task-indicator--${status.tone}${status.spinning ? " task-indicator--spinning" : ""}`}
                              title={status.label}
                              aria-label={status.label}
                            />
                          )}
                        </button>
                        {contextSessionId === session.id && (
                          <div className="task-context-menu" role="menu">
                            <button role="menuitem" onClick={() => { setContextSessionId(undefined); onRename(session); }}>编辑标题</button>
                            <button role="menuitem" className="danger-action" disabled={session.taskStatus === "in_progress"} onClick={() => { setContextSessionId(undefined); onDelete(session); }}>删除任务</button>
                          </div>
                        )}
                      </li>
                    })}
                  </ul>}
                </section>
              </li>
            ))}
          </ul>
        )}
      </nav>
      <button className="settings-entry" onClick={onOpenSettings} aria-label="打开设置">
        <GearIcon />
        <span>
          <strong>DeepSeek · {modelId}</strong>
          <small>{modelConfigured ? "已配置" : "未配置"}</small>
        </span>
      </button>
    </aside>
  );
}

function basename(path: string): string {
  return path.split("/").filter(Boolean).at(-1) ?? path;
}

function GearIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 8.25A3.75 3.75 0 1 0 12 15.75 3.75 3.75 0 0 0 12 8.25Zm8.1 5.2-1.54-.9a6.7 6.7 0 0 0 0-1.1l1.54-.9-1.5-2.6-1.55.9a7.06 7.06 0 0 0-.95-.55V5.5h-3v1.8a7.06 7.06 0 0 0-.95.55l-1.55-.9-1.5 2.6 1.54.9a6.7 6.7 0 0 0 0 1.1l-1.54.9 1.5 2.6 1.55-.9c.3.2.62.38.95.55v1.8h3v-1.8c.33-.17.65-.35.95-.55l1.55.9 1.5-2.6Z" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}

function FolderIcon({ open }: { open: boolean }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      {open ? (
        <path d="M3.5 8.5h6l1.6-2h3.7a2 2 0 0 1 1.8 1.1l.45.9h2.15a1.8 1.8 0 0 1 1.7 2.4l-2.35 6.6a2 2 0 0 1-1.9 1.35H5.4a2 2 0 0 1-1.9-2.6l2.05-6.4A2 2 0 0 1 7.45 8.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
      ) : (
        <path d="M3.5 6.5h6l1.7 2h7.3a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2v-10Z" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
      )}
    </svg>
  );
}
