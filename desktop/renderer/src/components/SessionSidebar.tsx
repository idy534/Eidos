import type { Session } from "../contracts";


interface Props {
  sessions: Session[];
  selectedId: string | undefined;
  disabled: boolean;
  onCreate: () => void;
  onSelect: (session: Session) => void;
}

export function SessionSidebar({ sessions, selectedId, disabled, onCreate, onSelect }: Props) {
  return (
    <aside className="sidebar" aria-label="Sessions">
      <div className="brand-row">
        <span className="brand-mark">E</span>
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

function basename(path: string): string {
  return path.split("/").filter(Boolean).at(-1) ?? path;
}
