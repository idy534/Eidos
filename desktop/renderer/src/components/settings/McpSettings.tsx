import React, { useState } from "react";
import type { McpServerRecord } from "../../contracts";
import type { SettingsPendingAction } from "./settings-types";
import { SettingSection } from "./SettingSection";
import { SettingRow } from "./SettingRow";
import { StatusBadge } from "./StatusBadge";
import { McpReviewDialog } from "./McpReviewDialog";
import { EmptySettingsState } from "./EmptySettingsState";

interface McpSettingsProps {
  servers: McpServerRecord[];
  pendingAction: SettingsPendingAction;
  onToggleMcp: (pluginId: string, serverId: string, enabled: boolean) => Promise<void>;
  onShowToast: (message: string, type: "success" | "info" | "error") => void;
}

export function McpSettings({
  servers,
  pendingAction,
  onToggleMcp,
  onShowToast,
}: McpSettingsProps) {
  const [reviewingServer, setReviewingServer] = useState<McpServerRecord | null>(null);
  const [expandedServerIds, setExpandedServerIds] = useState<Set<string>>(new Set());
  const [localError, setLocalError] = useState<string>();

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

  return (
    <div className="settings-panel">
      <div className="settings-panel-header">
        <h1>MCP Servers 扩展</h1>
        <p className="settings-panel-subtitle">
          MCP (Model Context Protocol) 允许 Agent 访问工具或数据源。启用敏感 Server 前需进行安全审阅与授权。
        </p>
      </div>

      {localError && <p className="setting-banner-error" role="alert">{localError}</p>}

      <SettingSection title="已声明的 MCP Servers">
        {servers.length === 0 ? (
          <EmptySettingsState
            title="没有 MCP Server 声明"
            description="导入包含 MCP 声明的 Plugin 后，可在本列表中审阅并授予执行权限。"
          />
        ) : (
          servers.map((server) => {
            const serverKey = `${server.pluginId}:${server.serverId}`;
            const isExpanded = expandedServerIds.has(serverKey);
            const isPending =
              pendingAction?.type === "toggle_mcp" &&
              pendingAction.pluginId === server.pluginId &&
              pendingAction.serverId === server.serverId;

            // Status logic
            let statusTone: "success" | "warning" | "danger" | "neutral" = "neutral";
            let statusLabel = "未授权";

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
              <SettingRow
                key={serverKey}
                disabled={isPending || !server.declaredEnabled}
                title={
                  <div className="mcp-row-header">
                    <span className="mcp-server-name">{server.serverId}</span>
                    <span className="mcp-plugin-tag">Plugin: {server.pluginId}</span>
                    <StatusBadge tone={statusTone}>{statusLabel}</StatusBadge>
                  </div>
                }
                description={
                  <div className="mcp-row-summary">
                    <span>权限 profile: <code>{server.permissionProfile}</code></span>
                    <span className="meta-divider">•</span>
                    <span>环境变量: {server.envNames.length ? server.envNames.join(", ") : "无"}</span>
                  </div>
                }
                action={
                  server.consented ? (
                    <button
                      type="button"
                      className="button-secondary"
                      disabled={isPending || !server.declaredEnabled}
                      onClick={() => void handleDisable(server)}
                    >
                      {isPending ? "停用中…" : "停用"}
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="button-primary"
                      disabled={isPending || !server.declaredEnabled}
                      onClick={() => setReviewingServer(server)}
                    >
                      {isPending ? "处理中…" : "审阅并启用"}
                    </button>
                  )
                }
                expandableDetails={
                  <dl className="mcp-details-grid">
                    <div>
                      <dt>Executable</dt>
                      <dd><code>{server.executable}</code></dd>
                    </div>
                    <div>
                      <dt>Arguments</dt>
                      <dd><code>{server.argv.join(" ") || "(none)"}</code></dd>
                    </div>
                    <div>
                      <dt>Startup Timeout</dt>
                      <dd>{server.startupTimeoutSeconds}s</dd>
                    </div>
                    <div>
                      <dt>Tool Timeout</dt>
                      <dd>{server.toolTimeoutSeconds}s</dd>
                    </div>
                    {server.errorCode && (
                      <div className="full-width">
                        <dt>Error Code</dt>
                        <dd className="error-text"><code>{server.errorCode}</code></dd>
                      </div>
                    )}
                  </dl>
                }
                isExpanded={isExpanded}
                onToggleExpand={() => toggleExpand(serverKey)}
              />
            );
          })
        )}
      </SettingSection>

      <McpReviewDialog
        server={reviewingServer}
        busy={pendingAction?.type === "toggle_mcp"}
        onConfirm={() => void handleConfirmEnable()}
        onCancel={() => setReviewingServer(null)}
      />
    </div>
  );
}
