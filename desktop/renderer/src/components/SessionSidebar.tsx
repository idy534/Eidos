import type { Run, Session } from "../contracts";
import { EidosMark } from "./EidosMark";


interface Props {
  sessions: Session[];
  selectedId: string | undefined;
  disabled: boolean;
  statusBySession: Record<string, Run["status"]>;
  onCreate: () => void;
  onSelect: (session: Session) => void;
}

export function SessionSidebar({ sessions, selectedId, disabled, statusBySession, onCreate, onSelect }: Props) {
  return (
    <aside className="sidebar" aria-label="Sessions">
      <div className="brand-row">
        <span className="brand-mark" aria-hidden="true">
          <EidosMark />
        </span>
        <span>Eidos</span>
      </div>
      <button className="new-session" disabled={disabled} onClick={onCreate}>＋ 新建 Session</button>
      <nav aria-label="历史 Sessions">
        <p className="nav-label">最近</p>
        {sessions.length === 0 ? (
          <p className="nav-empty">还没有 Session</p>
        ) : (
          <ul className="session-list">
            {sessions.map((session) => (
              <li key={session.id}>
                <button
                  className={session.id === selectedId ? "selected" : ""}
                  aria-current={session.id === selectedId ? "page" : undefined}
                  disabled={disabled}
                  onClick={() => onSelect(session)}
                >
                  <span>{basename(session.workspaceRoot)}</span>
                  <small>{session.workspaceRoot}</small>
                  {statusBySession[session.id] && (
                    <small className="session-status">{statusLabel(statusBySession[session.id]!)}</small>
                  )}
                </button>
              </li>
            ))}
          </ul>
        )}
      </nav>
      <p className="preview-note">MVP Lite · 本机单用户预览</p>
    </aside>
  );
}

function statusLabel(status: Run["status"]): string {
  return ({
    queued: "已排队", running: "执行中", waiting_approval: "等待批准",
    waiting_user_input: "等待输入", finalizing: "收尾中", stopped: "已停止",
    succeeded: "已完成", failed: "失败", canceled: "已取消", interrupted: "已中断",
  } as const)[status];
}

function basename(path: string): string {
  return path.split("/").filter(Boolean).at(-1) ?? path;
}
