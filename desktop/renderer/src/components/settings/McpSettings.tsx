import React, { useState } from "react";
import { Button } from "../Button.js";
import type { McpCreateInput, McpServerRecord, McpUpdateInput } from "../../contracts";
import type { SettingsPendingAction } from "./settings-types";
import { SettingSection } from "./SettingSection";
import { SettingRow } from "./SettingRow";
import { StatusBadge } from "./StatusBadge";
import { McpReviewDialog } from "./McpReviewDialog";
import { EmptySettingsState } from "./EmptySettingsState";
import { McpCreateDialog } from "./McpCreateDialog";

interface McpSettingsProps {
  servers: McpServerRecord[];
  pendingAction: SettingsPendingAction;
  onToggleMcp: (pluginId: string, serverId: string, enabled: boolean) => Promise<void>;
  onCreateMcp: (input: McpCreateInput) => Promise<void>;
  onUpdateMcp: (input: McpUpdateInput) => Promise<void>;
  onRemoveMcp: (serverId: string) => Promise<void>;
  onShowToast: (message: string, type: "success" | "info" | "error") => void;
}

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
  const [expandedServerIds, setExpandedServerIds] = useState<Set<string>>(new Set());
  const [localError, setLocalError] = useState<string>();
  const [createOpen, setCreateOpen] = useState(false);
  const [editingServer, setEditingServer] = useState<McpServerRecord | null>(null);

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

  async function handleCreate(input: McpCreateInput) {
    setLocalError(undefined);
    try {
      await onCreateMcp(input);
      onShowToast(`MCP Server “${input.serverId}” 已保存，请审阅后启用`, "success");
      setCreateOpen(false);
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

  return (
    <div className="settings-panel">
      <div className="settings-panel-header">
        <div className="header-title-with-stats">
          <div>
            <h1>MCP Servers 扩展</h1>
            <p className="settings-panel-subtitle">
              MCP (Model Context Protocol) 允许 Agent 访问工具或数据源。启用敏感 Server 前需进行安全审阅与授权。
            </p>
          </div>
          <Button
            variant="primary"
            size="medium"
            disabled={pendingAction?.type === "create_mcp"}
            loading={pendingAction?.type === "create_mcp"}
            onClick={() => setCreateOpen(true)}
          >
            添加 MCP
          </Button>
        </div>
      </div>

      {localError && <p className="setting-banner-error" role="alert">{localError}</p>}

      <SettingSection title="已声明的 MCP Servers">
        {servers.length === 0 ? (
          <EmptySettingsState
            title="没有 MCP Server 声明"
            description="你可以添加本地 STDIO MCP，也可以导入包含 MCP 声明的 Plugin。"
          />
        ) : (
          servers.map((server) => {
            const serverKey = `${server.pluginId}:${server.serverId}`;
            const isExpanded = expandedServerIds.has(serverKey);
            const isPending =
              (pendingAction?.type === "toggle_mcp" &&
                pendingAction.pluginId === server.pluginId &&
                pendingAction.serverId === server.serverId) ||
              ((pendingAction?.type === "update_mcp" || pendingAction?.type === "remove_mcp") &&
                pendingAction.serverId === server.serverId);

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
                    <span className="mcp-plugin-tag">
                      {server.pluginId === "manual" ? "手动配置" : `Plugin: ${server.pluginId}`}
                    </span>
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
                  <div className="mcp-row-actions">
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
                        审阅并启用
                      </Button>
                    )}
                  </div>
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
                    {server.cwd && (
                      <div className="full-width">
                        <dt>Working Directory</dt>
                        <dd><code>{server.cwd}</code></dd>
                      </div>
                    )}
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
      <McpCreateDialog
        open={createOpen}
        busy={pendingAction?.type === "create_mcp"}
        error={localError}
        onSave={handleCreate}
        onCancel={() => setCreateOpen(false)}
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
