import React, { useEffect, useRef } from "react";
import { Button } from "../Button.js";
import type { McpServerRecord } from "../../contracts";

interface McpReviewDialogProps {
  server: McpServerRecord | null;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function McpReviewDialog({
  server,
  busy = false,
  onConfirm,
  onCancel,
}: McpReviewDialogProps) {
  const confirmBtnRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (server) {
      const timer = setTimeout(() => confirmBtnRef.current?.focus(), 50);
      return () => clearTimeout(timer);
    }
  }, [server]);

  useEffect(() => {
    if (!server) return;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onCancel();
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [server, onCancel]);

  if (!server) return null;

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div
        className="modal-dialog modal-dialog--wide"
        role="dialog"
        aria-modal="true"
        aria-labelledby="mcp-review-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h3 id="mcp-review-title">启用 MCP Server</h3>
          <p className="modal-subtitle">请检查 MCP Server 的启动指令与所需权限</p>
        </div>

        <div className="modal-body mcp-review-body">
          <dl className="mcp-meta-grid">
            <div>
              <dt>Server 名称</dt>
              <dd><code>{server.pluginId === "manual" ? server.serverId : `${server.pluginId}:${server.serverId}`}</code></dd>
            </div>
            <div>
              <dt>来源</dt>
              <dd>{server.pluginId === "manual" ? "设置页手动配置" : `${server.pluginId} v${server.pluginVersion}`}</dd>
            </div>
            <div>
              <dt>权限配置</dt>
              <dd className="permission-badge">{server.permissionProfile}</dd>
            </div>
            <div>
              <dt>环境变量</dt>
              <dd>{server.envNames.length > 0 ? server.envNames.join(", ") : "无"}</dd>
            </div>
            <div>
              <dt>启动超时</dt>
              <dd>{server.startupTimeoutSeconds} 秒</dd>
            </div>
            <div>
              <dt>工具超时</dt>
              <dd>{server.toolTimeoutSeconds} 秒</dd>
            </div>
            {server.cwd && (
              <div className="full-width">
                <dt>工作目录</dt>
                <dd title={server.cwd}><code>{server.cwd}</code></dd>
              </div>
            )}
          </dl>
        </div>

        <div className="modal-footer">
          <Button
            variant="ghost"
            size="medium"
            disabled={busy}
            onClick={onCancel}
          >
            取消
          </Button>
          <Button
            ref={confirmBtnRef}
            variant="primary"
            size="medium"
            disabled={busy}
            loading={busy}
            onClick={onConfirm}
          >
            确认启用
          </Button>
        </div>
      </div>
    </div>
  );
}
