import { useEffect, useRef } from "react";
import type { McpToolDetail } from "../../../../shared/mcp-details.js";
import { CodeBlock } from "../CodeBlock.js";
import { CloseIcon } from "../MenuIcons.js";
import { useDialogFocusLifecycle } from "../useDialogFocusLifecycle.js";

interface McpSchemaDialogProps {
  serverName: string;
  tool: McpToolDetail | null;
  onClose: () => void;
}

export function McpSchemaDialog({ serverName, tool, onClose }: McpSchemaDialogProps) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  useDialogFocusLifecycle({ open: Boolean(tool), initialFocusRef: closeRef });

  useEffect(() => {
    if (!tool) return;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        onClose();
      }
    }
    window.addEventListener("keydown", handleKeyDown, true);
    return () => window.removeEventListener("keydown", handleKeyDown, true);
  }, [tool, onClose]);

  if (!tool) return null;

  return (
    <div className="modal-backdrop mcp-schema-backdrop" onClick={onClose}>
      <div
        ref={dialogRef}
        className="modal-dialog mcp-schema-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="mcp-schema-dialog-title"
        onClick={event => event.stopPropagation()}
      >
        <header className="mcp-schema-dialog__header">
          <div className="mcp-schema-dialog__heading">
            <h3 id="mcp-schema-dialog-title" className="mcp-schema-dialog__title">
              <span className="mcp-schema-dialog__server">{serverName}</span>
              <span className="mcp-schema-dialog__divider">/</span>
              <span className="mcp-schema-dialog__tool">{tool.name}</span>
            </h3>
            {tool.description ? (
              <p className="mcp-schema-dialog__description">{tool.description}</p>
            ) : null}
          </div>
          <button
            ref={closeRef}
            type="button"
            className="mcp-detail-close"
            aria-label={`关闭 ${tool.name} 详情`}
            onClick={onClose}
          >
            <CloseIcon />
          </button>
        </header>
        <div className="mcp-schema-dialog__body">
          <CodeBlock className="language-json">{JSON.stringify(tool.inputSchema, null, 2)}</CodeBlock>
        </div>
      </div>
    </div>
  );
}
