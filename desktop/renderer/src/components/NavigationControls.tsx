export interface NavigationControlsProps {
  sidebarOpen: boolean;
  canGoBack: boolean;
  canGoForward: boolean;
  onToggleSidebar: () => void;
  onGoBack: () => void;
  onGoForward: () => void;
  /** Whether to render the spacer that avoids macOS native traffic lights (86px) */
  showTrafficSpacer?: boolean;
  className?: string;
}

export function NavigationControls({
  sidebarOpen,
  canGoBack,
  canGoForward,
  onToggleSidebar,
  onGoBack,
  onGoForward,
  showTrafficSpacer = true,
  className = "",
}: NavigationControlsProps) {
  return (
    <div className={`navigation-controls ${className}`.trim()}>
      {showTrafficSpacer && <div className="window-traffic-spacer" aria-hidden="true" />}
      <div className="navigation-controls__buttons">
        <button
          type="button"
          className={`nav-icon-btn nav-sidebar-toggle${sidebarOpen ? " nav-sidebar-toggle--open" : ""}`}
          aria-label={sidebarOpen ? "收起侧边栏" : "展开侧边栏"}
          aria-expanded={sidebarOpen}
          title={sidebarOpen ? "收起侧边栏 (⌘B)" : "展开侧边栏 (⌘B)"}
          onClick={onToggleSidebar}
        >
          <svg viewBox="0 0 20 20" aria-hidden="true" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3.5" width="14" height="13" rx="3" />
            <path d="M7.5 3.5v13" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>

        <button
          type="button"
          className="nav-icon-btn nav-back-btn"
          aria-label="后退"
          title="后退 (⌘[)"
          disabled={!canGoBack}
          onClick={onGoBack}
        >
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <path d="M12.5 5L7.5 10L12.5 15" />
          </svg>
        </button>

        <button
          type="button"
          className="nav-icon-btn nav-forward-btn"
          aria-label="前进"
          title="前进 (⌘])"
          disabled={!canGoForward}
          onClick={onGoForward}
        >
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <path d="M7.5 5L12.5 10L7.5 15" />
          </svg>
        </button>
      </div>
    </div>
  );
}
