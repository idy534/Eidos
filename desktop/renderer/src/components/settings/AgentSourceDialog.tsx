import { useId, useRef } from "react";
import { createPortal } from "react-dom";
import type { AgentDefinitionDocument } from "../../../../shared/collaboration.generated.js";
import { Button } from "../Button.js";
import { useDialogFocusLifecycle } from "../useDialogFocusLifecycle.js";

export function AgentSourceDialog({ document: source, onClose }: { document: AgentDefinitionDocument; onClose(): void }) {
  const titleId = useId();
  const close = useRef<HTMLButtonElement>(null);
  const text = useRef<HTMLPreElement>(null);
  useDialogFocusLifecycle({ open: true, initialFocusRef: close });
  return createPortal(<div className="modal-backdrop">
    <div className="modal-dialog agent-editor" role="dialog" aria-modal="true" aria-labelledby={titleId} onKeyDown={(event) => {
      if (event.key === "Escape") { event.stopPropagation(); onClose(); }
      if (event.key === "Tab") { event.preventDefault(); (document.activeElement === close.current ? text.current : close.current)?.focus(); }
    }}>
      <header className="modal-header"><h2 id={titleId}>{source.definition.name} · 配置</h2><p className="modal-subtitle">{source.source}</p></header>
      <div className="modal-body"><pre className="agent-settings__source" ref={text} tabIndex={0}>{source.text}</pre></div>
      <footer className="modal-footer agent-editor__footer"><Button ref={close} onClick={onClose}>关闭</Button></footer>
    </div>
  </div>, document.body);
}
