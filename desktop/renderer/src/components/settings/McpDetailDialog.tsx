import { useEffect, useRef, useState, type ReactNode } from "react";
import type { McpServerRecord } from "../../contracts.js";
import type { McpToolDetail } from "../../../../shared/mcp-details.js";
import { useDialogFocusLifecycle } from "../useDialogFocusLifecycle.js";
import { SearchIcon } from "../FindBar.js";
import { CloseIcon } from "../MenuIcons.js";
import { StatusBadge } from "../StatusBadge.js";
import { McpToolRow } from "./McpToolRow.js";
import { McpSchemaDialog } from "./McpSchemaDialog.js";
import { useMcpTools } from "./useMcpTools.js";
import "./McpDetailDialog.css";

export function McpDetailDialog({
  server,
  onClose,
  icon,
}: {
  server: McpServerRecord | null;
  onClose: () => void;
  icon?: ReactNode;
}) {
  const state = useMcpTools(server);
  const [activeSchemaTool, setActiveSchemaTool] = useState<McpToolDetail | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const sentinelRef = useRef<HTMLDivElement>(null);

  useDialogFocusLifecycle({ open: Boolean(server), initialFocusRef: closeRef });

  useEffect(() => {
    if (!server) return;
    function keydown(event: KeyboardEvent) {
      if (activeSchemaTool) return;
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const targets = Array.from(
        dialogRef.current?.querySelectorAll<HTMLElement>(
          "button:not([disabled]), input, summary, [tabindex='0']"
        ) ?? []
      ).filter(element =>
        typeof element.checkVisibility === "function"
          ? element.checkVisibility({ checkVisibilityCSS: true })
          : element.getClientRects().length > 0
      );
      const first = targets[0];
      const last = targets.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    }
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [server, onClose, activeSchemaTool]);

  useEffect(() => {
    if (!server || !state.page?.nextCursor || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      entries => {
        if (entries.some(entry => entry.isIntersecting)) state.loadMore();
      },
      { root: listRef.current, rootMargin: "0px 0px 120px 0px" }
    );
    if (sentinelRef.current) observer.observe(sentinelRef.current);
    return () => observer.disconnect();
  }, [server, state.page?.nextCursor, state.loadMore]);

  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = 0;
  }, [state.query]);

  if (!server) return null;

  const authorized = server.consented && server.declaredEnabled;
  const label = !authorized
    ? "待启用"
    : !server.available
    ? "暂不可用"
    : state.page?.state === "ready"
    ? "可用"
    : "已授权";
  const description = state.description?.trim();

  return (
    <>
      <div className="modal-backdrop mcp-detail-backdrop" onClick={onClose}>
        <div
          ref={dialogRef}
          className="modal-dialog mcp-detail-dialog"
          role="dialog"
          aria-modal="true"
          aria-labelledby="mcp-detail-name"
          onClick={event => event.stopPropagation()}
        >
          <header className="mcp-detail-header">
            <div className="mcp-detail-icon" aria-hidden="true">{icon}</div>
            <div className="mcp-detail-heading">
              <div className="mcp-detail-title">
                <h2 id="mcp-detail-name">{server.serverId}</h2>
                <span className="mcp-pill-tag mcp-pill-tag--transport">STDIO</span>
                <StatusBadge tone={authorized && server.available ? "success" : "neutral"} dot>
                  {label}
                </StatusBadge>
              </div>
              <p className="mcp-detail-command">
                <code>{server.remote?.url || `$ ${[server.executable, ...server.argv].join(" ")}`}</code>
              </p>
              {description ? <p className="mcp-detail-description">{description}</p> : null}
            </div>
            <button
              ref={closeRef}
              type="button"
              className="mcp-detail-close"
              aria-label="关闭 MCP 详情"
              onClick={onClose}
            >
              <CloseIcon />
            </button>
          </header>

          <div className="mcp-detail-search">
            <SearchIcon />
            <input
              type="search"
              aria-label="搜索工具名称或描述"
              placeholder="搜索工具名称或描述..."
              maxLength={256}
              value={state.query}
              onChange={event => state.setQuery(event.target.value)}
              disabled={!authorized}
            />
          </div>

          <div className="mcp-detail-list-heading">
            <h3>工具列表</h3>
            {state.page?.state === "ready" && (
              <span>共 {state.page.total ?? state.tools.length} 条</span>
            )}
          </div>

          <div
            ref={listRef}
            className="mcp-detail-list"
            role="region"
            aria-label="MCP 工具列表"
            tabIndex={0}
            aria-busy={state.loading}
            onScroll={event => {
              const list = event.currentTarget;
              if (list.scrollHeight - list.scrollTop - list.clientHeight < 120) state.loadMore();
            }}
          >
            {state.page?.state === "not_authorized" && (
              <p className="mcp-detail-message">
                此服务尚未启用或授权。请先在列表中启用服务，再获取工具列表。
              </p>
            )}
            {state.tools.map(tool => (
              <McpToolRow key={tool.name} tool={tool} onOpenSchema={setActiveSchemaTool} />
            ))}
            {state.error && (
              <div className="mcp-detail-message" role="alert">
                <p>{state.error}</p>
                <button type="button" onClick={state.retry}>重试</button>
              </div>
            )}
            {state.loading && (
              <p className="mcp-detail-message" role="status">
                {state.tools.length ? "正在加载更多工具…" : "正在获取工具列表…"}
              </p>
            )}
            {!state.loading && !state.error && state.page?.state === "ready" && (
              <p className="mcp-detail-message" role="status">
                {state.page.total === 0
                  ? state.query
                    ? "没有找到匹配的工具。"
                    : "此服务没有提供工具。"
                  : !state.page.nextCursor
                  ? "已全部加载"
                  : "向下滚动加载更多"}
              </p>
            )}
            <div ref={sentinelRef} aria-hidden="true" className="mcp-detail-sentinel" />
          </div>

          <div className="mcp-detail-note">
            <span className="mcp-detail-note__icon" aria-hidden="true">ⓘ</span>
            <span>工具信息以最近一次获取结果为准。</span>
          </div>
        </div>
      </div>
      <McpSchemaDialog
        serverName={server.serverId}
        tool={activeSchemaTool}
        onClose={() => setActiveSchemaTool(null)}
      />
    </>
  );
}
