import { useEffect, useRef, useState } from "react";

import { Button } from "./Button.js";
import { useDialogFocusLifecycle } from "./useDialogFocusLifecycle.js";

interface CreateProjectDialogProps {
  open: boolean;
  sourceFolder?: string | undefined;
  busy?: boolean;
  error?: string | undefined;
  onCreate: (name: string | undefined, sourceFolder: string) => void;
  onSelectFolder: () => void;
  onCancel: () => void;
  getFallbackFocus?: (() => HTMLElement | null) | undefined;
}

export function CreateProjectDialog({
  open,
  sourceFolder,
  busy = false,
  error,
  onCreate,
  onSelectFolder,
  onCancel,
  getFallbackFocus,
}: CreateProjectDialogProps) {
  const [name, setName] = useState("");
  const dialogRef = useRef<HTMLDivElement>(null);

  useDialogFocusLifecycle({
    open,
    getFallbackFocus,
  });

  useEffect(() => {
    if (open) setName("");
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) {
        event.preventDefault();
        onCancel();
      }
      if (event.key === "Tab" && dialogRef.current) {
        const focusable = Array.from(
          dialogRef.current.querySelectorAll<HTMLElement>(
            "button:not([disabled]), input:not([disabled])",
          ),
        );
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (!first || !last) return;
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [busy, onCancel, open]);

  if (!open) return null;

  const trimmedName = name.trim();
  const canCreate = Boolean(sourceFolder?.trim() && !busy);

  return (
    <div className="modal-backdrop" onClick={busy ? undefined : onCancel}>
      <div
        ref={dialogRef}
        className="modal-dialog create-project-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-project-dialog-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="create-project-dialog-header">
          <h3 id="create-project-dialog-title">创建项目</h3>
          <button type="button" className="create-project-dialog-close" aria-label="关闭" onClick={onCancel} disabled={busy}>×</button>
        </div>
        <div className="create-project-dialog-body">
          <label className="create-project-name-field">
            <FolderIcon />
            <span className="sr-only">项目名称（可选）</span>
            <input
              aria-label="项目名称（可选）"
              placeholder="项目名称（可选）"
              value={name}
              maxLength={120}
              disabled={busy}
              onChange={(event) => setName(event.target.value)}
            />
          </label>

          <div className="create-project-source">
            <h4>源文件夹</h4>
            <button
              type="button"
              className={`create-project-folder-button${sourceFolder ? " create-project-folder-button--selected" : ""}`}
              disabled={busy}
              onClick={onSelectFolder}
            >
              <FolderPlusIcon />
              <span>{sourceFolder || "添加 Eidos 可读写的文件夹"}</span>
            </button>
          </div>
          {error && <p className="setting-field-error" role="alert">{error}</p>}
        </div>
        <div className="create-project-dialog-footer">
          <Button variant="ghost" disabled={busy} onClick={onCancel}>取消</Button>
          <Button
            variant="primary"
            loading={busy}
            disabled={!canCreate}
            onClick={() => onCreate(trimmedName || undefined, sourceFolder!.trim())}
          >
            创建项目
          </Button>
        </div>
      </div>
    </div>
  );
}

function FolderIcon() {
  return (
    <svg className="create-project-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M3.5 6.5c0-1.1.9-2 2-2h5l2 2h6c1.1 0 2 .9 2 2v8.5c0 1.1-.9 2-2 2h-13c-1.1 0-2-.9-2-2V6.5Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}

function FolderPlusIcon() {
  return (
    <svg className="create-project-folder-icon" viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <path d="M5 9c0-1.1.9-2 2-2h7l2.5 3H25c1.1 0 2 .9 2 2v10c0 1.1-.9 2-2 2H7c-1.1 0-2-.9-2-2V9Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M20 14v7M16.5 17.5h7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}
