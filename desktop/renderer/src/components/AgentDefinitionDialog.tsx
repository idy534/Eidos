import { useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";
import type { InputPreview } from "../../../shared/input-context.js";
import { AgentIcon } from "./InputPicker.js";

export function AgentDefinitionDialog({ preview, onClose }: { preview: InputPreview; onClose(): void }) {
  const titleId = useId();
  const closeRef = useRef<HTMLButtonElement>(null);
  const bodyRef = useRef<HTMLPreElement>(null);
  useEffect(() => {
    const trigger = document.activeElement;
    closeRef.current?.focus();
    return () => { if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus(); };
  }, []);
  return createPortal(
    <div className="modal-backdrop skill-detail-backdrop" onClick={onClose}>
      <div className="skill-detail-dialog agent-definition-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId}
        onClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => {
          if (event.key === "Escape") { event.stopPropagation(); onClose(); }
          if (event.key === "Tab") {
            event.preventDefault();
            (document.activeElement === closeRef.current ? bodyRef.current : closeRef.current)?.focus();
          }
        }}>
        <div className="skill-detail-header">
          <div className="skill-detail-header-left">
            <span className="skill-icon agent-icon" aria-hidden="true">
              <AgentIcon width={18} height={18} />
            </span>
            <div className="skill-detail-title-group">
              <h2 id={titleId}>{preview.reference.label}</h2>
              <span className="skill-type-label">Agent</span>
            </div>
          </div>
          <div className="skill-detail-header-right">
            <div className="skill-detail-actions">
              <button
                ref={closeRef}
                type="button"
                className="skill-detail-close-btn"
                aria-label="关闭 Agent 定义"
                onClick={onClose}
              >
                ×
              </button>
            </div>
          </div>
        </div>
        <p className="skill-detail-description">本次引用保存的定义。父任务通过现有子 Agent 工具调用它。</p>
        <div className="skill-detail-body">
          <pre ref={bodyRef} tabIndex={0} className="agent-definition-content" aria-label="Agent 配置和指令">{preview.text}</pre>
        </div>
      </div>
    </div>, document.body,
  );
}
