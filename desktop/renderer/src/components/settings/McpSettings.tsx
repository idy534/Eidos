import React, { useState } from "react";
import { Button } from "../Button.js";
import { CodeBlock } from "../CodeBlock.js";
import type { McpCreateInput, McpServerRecord, McpUpdateInput } from "../../contracts";
import type { SettingsPendingAction } from "./settings-types";
import { StatusBadge } from "../StatusBadge.js";
import { AlertBanner } from "../AlertBanner.js";
import { McpReviewDialog } from "./McpReviewDialog";
import { McpCreateDialog } from "./McpCreateDialog";
import { McpDetailDialog } from "./McpDetailDialog.js";

interface McpSettingsProps {
  servers: McpServerRecord[];
  pendingAction: SettingsPendingAction;
  onToggleMcp: (pluginId: string, serverId: string, enabled: boolean) => Promise<void>;
  onCreateMcp: (input: McpCreateInput) => Promise<void>;
  onUpdateMcp: (input: McpUpdateInput) => Promise<void>;
  onRemoveMcp: (serverId: string) => Promise<void>;
  onShowToast: (message: string, type: "success" | "info" | "error") => void;
}

/* ==========================================================================
   SVG ICONS - macOS Native Vector Icons
   ========================================================================== */


function BrowserIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      <circle cx="8" cy="8" r="6.2" />
      <path d="M1.8 8h12.4M8 1.8a9.2 9.2 0 0 0 0 12.4M8 1.8a9.2 9.2 0 0 1 0 12.4" />
    </svg>
  );
}

function FolderIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      <path d="M2 3.5C2 2.67 2.67 2 3.5 2H6.2c.4 0 .78.16 1.06.44l1.3 1.3c.28.28.66.44 1.06.44H12.5c.83 0 1.5.67 1.5 1.5V12.5c0 .83-.67 1.5-1.5 1.5H3.5c-.83 0-1.5-.67-1.5-1.5V3.5z" />
    </svg>
  );
}

function DatabaseIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      <ellipse cx="8" cy="4" rx="5.5" ry="2" />
      <path d="M2.5 4v4c0 1.1 2.46 2 5.5 2s5.5-.9 5.5-2V4" />
      <path d="M2.5 8v4c0 1.1 2.46 2 5.5 2s5.5-.9 5.5-2V8" />
    </svg>
  );
}

function NetworkIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      <path d="M8.5 1.5 2.5 8.5h5l-1 6 7-8h-5l1-5z" />
    </svg>
  );
}

function GitIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      <circle cx="4" cy="4" r="1.8" />
      <circle cx="4" cy="12" r="1.8" />
      <circle cx="12" cy="5.5" r="1.8" />
      <path d="M4 5.8v4.4" />
      <path d="M4 8.5a4 4 0 0 1 4-4h2.2" />
    </svg>
  );
}

function TerminalIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      <rect x="2" y="2.5" width="12" height="11" rx="2" />
      <path d="m4.8 5.8 2.2 2.2-2.2 2.2M8.8 10.2h2.4" />
    </svg>
  );
}

function ShieldIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      <path d="M8 1.5 2.5 3.8v4.4c0 3.8 2.5 6.1 5.5 6.8 3-.7 5.5-3 5.5-6.8V3.8L8 1.5z" />
    </svg>
  );
}

function KeyIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      <circle cx="5.5" cy="8" r="3" />
      <path d="M8.5 8H14v2h-2v2h-2V8" />
    </svg>
  );
}

function TimerIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      <circle cx="8" cy="8.5" r="5.5" />
      <path d="M8 5.5v3l2 1M6 1.5h4" />
    </svg>
  );
}

function SearchIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      <circle cx="7" cy="7" r="4.5" />
      <path d="m10.5 10.5 3.5 3.5" />
    </svg>
  );
}




function WarningIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      <path d="M8 2.2 1.5 13.5h13L8 2.2zM8 6.5v3.2M8 11.8h.01" />
    </svg>
  );
}

/* ==========================================================================
   CATEGORY & PRESETS
   ========================================================================== */

function getCategoryInfo(serverId: string, executable: string) {
  const s = `${serverId} ${executable}`.toLowerCase();
  if (s.includes("playwright") || s.includes("browser") || s.includes("puppeteer") || s.includes("chrome")) {
    return { type: "browser", label: "Browser", icon: BrowserIcon };
  }
  if (s.includes("filesystem") || s.includes("file") || s.includes("dir") || s.includes("fs")) {
    return { type: "filesystem", label: "Filesystem", icon: FolderIcon };
  }
  if (s.includes("sqlite") || s.includes("postgres") || s.includes("mysql") || s.includes("sql") || s.includes("db")) {
    return { type: "database", label: "Database", icon: DatabaseIcon };
  }
  if (s.includes("fetch") || s.includes("http") || s.includes("api") || s.includes("curl") || s.includes("web")) {
    return { type: "network", label: "Network API", icon: NetworkIcon };
  }
  if (s.includes("git") || s.includes("github") || s.includes("gitlab")) {
    return { type: "git", label: "Git VCS", icon: GitIcon };
  }
  return { type: "default", label: "CLI Tool", icon: TerminalIcon };
}

interface McpPreset {
  id: string;
  name: string;
  category: "browser" | "filesystem" | "database" | "network";
  executable: string;
  argv: string[];
  permissionProfile: "connector" | "workspace_read";
  desc: string;
}

const MCP_PRESETS: McpPreset[] = [
  {
    id: "playwright",
    name: "Playwright",
    category: "browser",
    executable: "npx",
    argv: ["-y", "@modelcontextprotocol/server-playwright"],
    permissionProfile: "connector",
    desc: "网页抓取、UI 交互测试与端到端自动化审查",
  },
  {
    id: "filesystem",
    name: "Filesystem",
    category: "filesystem",
    executable: "npx",
    argv: ["-y", "@modelcontextprotocol/server-filesystem", "~/Documents"],
    permissionProfile: "workspace_read",
    desc: "安全读取和分析本地指定受控工作目录",
  },
  {
    id: "fetch",
    name: "Fetch",
    category: "network",
    executable: "uvx",
    argv: ["mcp-server-fetch"],
    permissionProfile: "connector",
    desc: "转换与读取外部网页内容及公开 HTTP 数据",
  },
  {
    id: "sqlite",
    name: "SQLite",
    category: "database",
    executable: "uvx",
    argv: ["mcp-server-sqlite", "--db-path", "~/workspace.db"],
    permissionProfile: "workspace_read",
    desc: "查询本地 SQLite 数据表结构与执行 SQL 语句",
  },
];

/* ==========================================================================
   COMPONENT IMPLEMENTATION
   ========================================================================== */

export function McpSettings({
  servers,
  pendingAction,
  onToggleMcp,
  onCreateMcp,
  onUpdateMcp,
  onRemoveMcp,
  onShowToast,
}: McpSettingsProps) {
  const [reviewingServer, setReviewingServer] = useState<McpServerRecord | null>(null);
  const [detailKey, setDetailKey] = useState<string | null>(null);
  const [expandedServerIds, setExpandedServerIds] = useState<Set<string>>(new Set());
  const [localError, setLocalError] = useState<string>();
  const [createOpen, setCreateOpen] = useState(false);
  const [editingServer, setEditingServer] = useState<McpServerRecord | null>(null);
  const [presetDraft, setPresetDraft] = useState<Partial<McpCreateInput> | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [filterTab, setFilterTab] = useState<"all" | "active" | "pending">("all");

  function toggleExpand(key: string) {
    setExpandedServerIds((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }

  function handleOpenWithPreset(preset: McpPreset) {
    setPresetDraft({
      serverId: preset.id,
      executable: preset.executable,
      argv: preset.argv,
      permissionProfile: preset.permissionProfile,
      env: {},
      envNames: [],
      startupTimeoutSeconds: 15,
      toolTimeoutSeconds: 60,
    });
    setLocalError(undefined);
    setCreateOpen(true);
  }

  async function handleConfirmEnable() {
    if (!reviewingServer) return;
    setLocalError(undefined);
    try {
      await onToggleMcp(reviewingServer.pluginId, reviewingServer.serverId, true);
      onShowToast(`MCP Server “${reviewingServer.serverId}” 已授权并启用`, "success");
      setReviewingServer(null);
    } catch (cause) {
      const msg = cause instanceof Error ? cause.message : "启用 MCP Server 失败";
      setLocalError(msg);
    }
  }

  async function handleDisable(server: McpServerRecord) {
    setLocalError(undefined);
    try {
      await onToggleMcp(server.pluginId, server.serverId, false);
      onShowToast(`MCP Server “${server.serverId}” 已停用`, "info");
    } catch (cause) {
      const msg = cause instanceof Error ? cause.message : "停用 MCP Server 失败";
      setLocalError(msg);
    }
  }

  async function handleCreate(input: McpCreateInput) {
    setLocalError(undefined);
    try {
      await onCreateMcp(input);
      onShowToast(`MCP Server “${input.serverId}” 已保存，请审阅后启用`, "success");
      setCreateOpen(false);
      setPresetDraft(null);
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : "保存 MCP Server 失败");
      throw cause;
    }
  }

  async function handleUpdate(input: McpUpdateInput) {
    setLocalError(undefined);
    try {
      await onUpdateMcp(input);
      onShowToast(`MCP Server “${input.serverId}” 已保存，请重新审阅后启用`, "success");
      setEditingServer(null);
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : "保存 MCP Server 失败");
      throw cause;
    }
  }

  async function handleRemove(serverId: string) {
    setLocalError(undefined);
    try {
      await onRemoveMcp(serverId);
      onShowToast(`MCP Server “${serverId}” 已卸载`, "success");
      setEditingServer(null);
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : "卸载 MCP Server 失败");
      throw cause;
    }
  }

  // Dashboard Metrics
  const totalCount = servers.length;
  const activeCount = servers.filter((s) => s.consented && s.available).length;
  const pendingCount = servers.filter((s) => !s.consented).length;

  // Filtering
  const filteredServers = servers.filter((server) => {
    // Tab filter
    if (filterTab === "active" && (!server.consented || !server.available)) return false;
    if (filterTab === "pending" && server.consented) return false;

    // Search query
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase().trim();
      const matchName = server.serverId.toLowerCase().includes(q);
      const matchExec = server.executable.toLowerCase().includes(q);
      const matchPlugin = server.pluginId.toLowerCase().includes(q);
      const matchArgs = server.argv.join(" ").toLowerCase().includes(q);
      const matchProfile = server.permissionProfile.toLowerCase().includes(q);
      return matchName || matchExec || matchPlugin || matchArgs || matchProfile;
    }
    return true;
  });

  const detailServer = servers.find(server => `${server.pluginId}:${server.serverId}` === detailKey) ?? null;
  const DetailIcon = detailServer ? getCategoryInfo(detailServer.serverId, detailServer.executable).icon : TerminalIcon;

  return (
    <div className="settings-panel mcp-settings-panel">
      {/* Header Area */}
      <div className="settings-panel-header">
        <div className="header-title-with-stats">
          <div>
            <h1>MCP Servers 扩展</h1>
            <p className="settings-panel-subtitle">
              MCP (Model Context Protocol) 允许 Agent 访问外部工具或数据源。启用敏感 Server 前需进行安全审阅与授权。
            </p>
          </div>
          <Button
            variant="primary"
            size="medium"
            disabled={pendingAction?.type === "create_mcp"}
            loading={pendingAction?.type === "create_mcp"}
            onClick={() => {
              setPresetDraft(null);
              setCreateOpen(true);
            }}
          >
            添加 MCP
          </Button>
        </div>
      </div>

      {localError && (
        <AlertBanner tone="error">
          {localError}
        </AlertBanner>
      )}

      {/* Main Content Area */}
      {servers.length === 0 ? (
        <div className="mcp-empty-state">
          <div className="mcp-empty-icon-wrap">
            <TerminalIcon />
          </div>
          <h2 className="mcp-empty-title">没有 MCP Server 声明</h2>
          <p className="mcp-empty-desc">
            你可以添加本地 STDIO MCP。插件自带的服务请在插件详情中管理。
          </p>
          <Button
            variant="primary"
            size="medium"
            onClick={() => {
              setPresetDraft(null);
              setCreateOpen(true);
            }}
          >
            添加自定义 MCP
          </Button>

          {/* Quick Starter Presets */}
          <div className="mcp-presets-container">
            <div className="mcp-presets-heading">常用 MCP 快速起步推荐</div>
            <div className="mcp-presets-grid">
              {MCP_PRESETS.map((preset) => {
                const CategoryIcon =
                  preset.category === "browser"
                    ? BrowserIcon
                    : preset.category === "filesystem"
                    ? FolderIcon
                    : preset.category === "database"
                    ? DatabaseIcon
                    : NetworkIcon;
                return (
                  <div
                    key={preset.id}
                    className="mcp-preset-card"
                    role="button"
                    tabIndex={0}
                    onClick={() => handleOpenWithPreset(preset)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        handleOpenWithPreset(preset);
                      }
                    }}
                  >
                    <div className={`mcp-preset-icon mcp-card__avatar--${preset.category}`}>
                      <CategoryIcon />
                    </div>
                    <div className="mcp-preset-info">
                      <span className="mcp-preset-name">{preset.name}</span>
                      <span className="mcp-preset-cmd">{preset.executable} {preset.argv.join(" ")}</span>
                      <span className="mcp-preset-desc">{preset.desc}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      ) : (
        <>
          {/* Toolbar with Search and Filter Segment */}
          <div className="mcp-toolbar">
            <div className="mcp-search-wrap">
              <SearchIcon className="mcp-search-icon" />
              <input
                type="search"
                className="mcp-search-input"
                placeholder="搜索 MCP Server、命令或参数..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
              {searchQuery && (
                <button
                  type="button"
                  className="mcp-search-clear"
                  onClick={() => setSearchQuery("")}
                  title="清除搜索"
                >
                  ×
                </button>
              )}
            </div>

            <div className="mcp-filter-tabs">
              <button
                type="button"
                className={`mcp-filter-btn ${filterTab === "all" ? "is-active" : ""}`}
                onClick={() => setFilterTab("all")}
              >
                全部 ({totalCount})
              </button>
              <button
                type="button"
                className={`mcp-filter-btn ${filterTab === "active" ? "is-active" : ""}`}
                onClick={() => setFilterTab("active")}
              >
                已启用 ({activeCount})
              </button>
              <button
                type="button"
                className={`mcp-filter-btn ${filterTab === "pending" ? "is-active" : ""}`}
                onClick={() => setFilterTab("pending")}
              >
                待启用 ({pendingCount})
              </button>
            </div>
          </div>

          {/* Cards List */}
          {filteredServers.length === 0 ? (
            <div className="mcp-empty-search">
              <p>未找到匹配 “{searchQuery}” 的 MCP Server</p>
              <Button variant="ghost" size="small" onClick={() => { setSearchQuery(""); setFilterTab("all"); }}>
                重置筛选条件
              </Button>
            </div>
          ) : (
            <div className="mcp-cards-list">
              {filteredServers.map((server) => {
                const serverKey = `${server.pluginId}:${server.serverId}`;
                const isExpanded = expandedServerIds.has(serverKey);
                const isPending =
                  (pendingAction?.type === "toggle_mcp" &&
                    pendingAction.pluginId === server.pluginId &&
                    pendingAction.serverId === server.serverId) ||
                  ((pendingAction?.type === "update_mcp" || pendingAction?.type === "remove_mcp") &&
                    pendingAction.serverId === server.serverId);

                const category = getCategoryInfo(server.serverId, server.executable);
                const CategoryIcon = category.icon;
                const fullCommand = [server.executable, ...server.argv].join(" ");

                // Status logic
                let statusTone: "success" | "warning" | "danger" | "neutral" = "neutral";
                let statusLabel = "待启用";

                if (server.errorCode) {
                  statusTone = "danger";
                  statusLabel = `异常: ${server.errorCode}`;
                } else if (server.consented && server.available) {
                  statusTone = "success";
                  statusLabel = "可用";
                } else if (server.consented && !server.available) {
                  statusTone = "warning";
                  statusLabel = "已授权但不可用";
                } else if (!server.declaredEnabled) {
                  statusTone = "neutral";
                  statusLabel = "Plugin 处已停用";
                }

                return (
                  <div
                    key={serverKey}
                    className={`mcp-card ${!server.declaredEnabled ? "mcp-card--disabled" : ""}`}
                  >
                    {/* Main Row */}
                    <div
                      className="mcp-card__main mcp-card__main--clickable"
                      onClick={() => setDetailKey(serverKey)}
                    >
                      {/* Avatar Icon */}
                      <div className={`mcp-card__avatar mcp-card__avatar--${category.type}`}>
                        <CategoryIcon />
                      </div>

                      {/* Info & Specs */}
                      <div className="mcp-card__info">
                        <div className="mcp-card__title-row">
                          <h2 className="mcp-card__name">
                            <button
                              type="button"
                              className="mcp-card__name-button"
                              aria-label={`查看 ${server.serverId} 工具`}
                              onClick={(e) => {
                                e.stopPropagation();
                                setDetailKey(serverKey);
                              }}
                            >
                              {server.serverId}
                            </button>
                          </h2>
                          {server.pluginId !== "manual" && (
                            <span className="mcp-pill-tag">Plugin: {server.pluginId}</span>
                          )}
                          <span className="mcp-pill-tag mcp-pill-tag--transport">STDIO</span>
                          <StatusBadge tone={statusTone} dot>
                            {statusLabel}
                          </StatusBadge>
                        </div>
                      </div>

                      {/* Right Action Buttons */}
                      <div
                        className="mcp-card__actions"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {server.pluginId === "manual" && (
                          <Button
                            variant="ghost"
                            size="medium"
                            disabled={isPending || !server.declaredEnabled}
                            onClick={() => {
                              setLocalError(undefined);
                              setEditingServer(server);
                            }}
                          >
                            编辑
                          </Button>
                        )}

                        {server.consented ? (
                          <Button
                            variant="ghost"
                            size="medium"
                            disabled={isPending || !server.declaredEnabled}
                            loading={isPending && pendingAction?.type === "toggle_mcp"}
                            onClick={() => void handleDisable(server)}
                          >
                            停用
                          </Button>
                        ) : (
                          <Button
                            variant="primary"
                            size="medium"
                            disabled={isPending || !server.declaredEnabled}
                            loading={isPending && pendingAction?.type === "toggle_mcp"}
                            onClick={() => setReviewingServer(server)}
                          >
                            启用
                          </Button>
                        )}
                      </div>
                    </div>

                    {/* Card Footer: 其他设置 (Web Search Style) */}
                    <div className="mcp-card__footer">
                      <button
                        type="button"
                        className="mcp-card__expand-btn"
                        aria-expanded={isExpanded}
                        onClick={() => toggleExpand(serverKey)}
                      >
                        <span className="mcp-card__expand-title">
                          <svg
                            className={`mcp-chevron ${isExpanded ? "mcp-chevron--open" : ""}`}
                            width="12"
                            height="12"
                            viewBox="0 0 12 12"
                            fill="none"
                            aria-hidden="true"
                          >
                            <path
                              d="M2.5 4.5L6 8L9.5 4.5"
                              stroke="currentColor"
                              strokeWidth="1.5"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                            />
                          </svg>
                          其他设置
                        </span>
                      </button>

                      {/* Expandable Technical Details Console */}
                      {isExpanded && (
                        <div className="mcp-card__details">
                          {/* 1. Terminal / Shell Command Block */}
                          <CodeBlock className="language-bash" title="启动指令">
                            {fullCommand}
                          </CodeBlock>

                          {/* 2. Specs Metric Cards Grid */}
                          <dl className="mcp-specs-grid">
                            <div className="mcp-spec-tile mcp-spec-tile--wide">
                              <dt>
                                <FolderIcon /> 工作目录 (CWD)
                              </dt>
                              <dd title={server.cwd || `默认 ~/Eidos/eidos-mcp/${server.serverId}`}>
                                {server.cwd ? (
                                  <code>{server.cwd}</code>
                                ) : (
                                  <span className="mcp-spec-muted">默认 (~/Eidos/eidos-mcp/{server.serverId})</span>
                                )}
                              </dd>
                            </div>

                            <div className="mcp-spec-tile">
                              <dt>
                                <TimerIcon /> 启动超时
                              </dt>
                              <dd>{server.startupTimeoutSeconds} 秒</dd>
                            </div>

                            <div className="mcp-spec-tile">
                              <dt>
                                <TimerIcon /> 工具超时
                              </dt>
                              <dd>{server.toolTimeoutSeconds} 秒</dd>
                            </div>

                            <div className="mcp-spec-tile">
                              <dt>
                                <ShieldIcon /> 权限范围
                              </dt>
                              <dd>
                                <code>{server.permissionProfile}</code>
                              </dd>
                            </div>

                            <div className="mcp-spec-tile mcp-spec-tile--wide">
                              <dt>
                                <KeyIcon /> 声明的环境变量
                              </dt>
                              <dd>
                                {server.envNames.length > 0 ? (
                                  <code>{server.envNames.join(", ")}</code>
                                ) : (
                                  <span className="mcp-spec-muted">无</span>
                                )}
                              </dd>
                            </div>
                          </dl>

                          {/* 3. Error Callout if error code */}
                          {server.errorCode && (
                            <div className="mcp-error-callout" role="alert">
                              <WarningIcon />
                              <div className="mcp-error-content">
                                <strong>运行异常错误码: <code>{server.errorCode}</code></strong>
                                <p>请核实启动命令的依赖环境（如 Node.js / npx 或 Python uvx）以及指定的工作目录路径有效性。</p>
                              </div>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}

      {/* Dialogs */}
      <McpDetailDialog server={detailServer} icon={<DetailIcon />} onClose={() => setDetailKey(null)} />
      <McpReviewDialog
        server={reviewingServer}
        busy={pendingAction?.type === "toggle_mcp"}
        onConfirm={() => void handleConfirmEnable()}
        onCancel={() => setReviewingServer(null)}
      />
      <McpCreateDialog
        open={createOpen}
        busy={pendingAction?.type === "create_mcp"}
        initialPreset={presetDraft}
        error={localError}
        onSave={handleCreate}
        onCancel={() => {
          setCreateOpen(false);
          setPresetDraft(null);
        }}
      />
      <McpCreateDialog
        open={editingServer !== null}
        server={editingServer}
        busy={pendingAction?.type === "update_mcp" && pendingAction.serverId === editingServer?.serverId}
        removeBusy={pendingAction?.type === "remove_mcp" && pendingAction.serverId === editingServer?.serverId}
        error={localError}
        onSave={handleUpdate}
        onRemove={handleRemove}
        onCancel={() => setEditingServer(null)}
      />
    </div>
  );
}
