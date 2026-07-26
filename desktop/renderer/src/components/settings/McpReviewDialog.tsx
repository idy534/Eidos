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

  const fullCommand = [server.executable, ...server.argv].join(" ");

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
          <h3 id="mcp-review-title">审阅并启用 MCP Server</h3>
          <p className="modal-subtitle">请检查 MCP Server 的启动指令与所需权限</p>
        </div>

        <div className="modal-body mcp-review-body">
          <div className="mcp-warning-banner">
            <span className="mcp-warning-icon">⚠️</span>
            <p>启用后，该 MCP Server 可能在任务执行过程中被调用。</p>
          </div>

          <dl className="mcp-meta-grid">
            <div>
              <dt>Server 名称</dt>
              <dd><code>{server.pluginId}:{server.serverId}</code></dd>
            </div>
            <div>
              <dt>来源 Plugin</dt>
              <dd>{server.pluginId} v{server.pluginVersion}</dd>
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
          </dl>

          <div className="mcp-command-block">
            <label>完整执行命令</label>
            <pre><code>{fullCommand}</code></pre>
          </div>
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
            确认并启用
          </Button>
        </div>
      </div>
    </div>
  );
}
