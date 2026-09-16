import { useEffect, useRef, useState } from "react";
import type { SkillDetail, SkillMetadata, SkillRemoval } from "../../contracts.js";
import { userFacingError } from "../../session-state.js";
import { ArtifactProvider } from "../ArtifactContext.js";
import { Button } from "../Button.js";
import { DropdownMenu } from "../DropdownMenu.js";
import { MarkdownContent } from "../MarkdownContent.js";
import { useDialogFocusLifecycle } from "../useDialogFocusLifecycle.js";
import { ConfirmDialog } from "./ConfirmDialog.js";
import { Toggle } from "./Toggle.js";
import type { SettingsPendingAction } from "./settings-types.js";

export function SkillInitial({ name }: { name: string }) {
  return <span className="skill-initial" aria-hidden="true">{Array.from(name.trim())[0]?.toLocaleUpperCase() ?? "S"}</span>;
}

interface SkillDetailDialogProps {
  skill: SkillMetadata | undefined;
  pendingAction: SettingsPendingAction;
  storageReady: boolean;
  onToggleSkill: (id: string, enabled: boolean) => Promise<void>;
  onRemoveSkill: (id: string) => Promise<SkillRemoval>;
  onShowToast: (message: string, type?: "success" | "info" | "error") => void;
  onClose: () => void;
  getFallbackFocus: () => HTMLElement | null;
}

export function SkillDetailDialog({
  skill, pendingAction, storageReady, onToggleSkill, onRemoveSkill,
  onShowToast, onClose, getFallbackFocus,
}: SkillDetailDialogProps) {
  const [detail, setDetail] = useState<SkillDetail>();
  const [error, setError] = useState<string>();
  const [loadError, setLoadError] = useState<string>();
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [reload, setReload] = useState(0);
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const busy = Boolean(pendingAction);
  const id = skill?.qualifiedId;
  const currentDetail = detail?.qualifiedId === id ? detail : undefined;
  useDialogFocusLifecycle({ open: Boolean(skill), initialFocusRef: closeRef, getFallbackFocus });

  useEffect(() => {
    setDetail(undefined);
    setError(undefined);
    setLoadError(undefined);
    setConfirmRemove(false);
    if (!id) return;
    let active = true;
    void window.eidosRuntime.readSkillDetail(id).then(
      (result) => { if (active) setDetail(result); },
      (cause: unknown) => { if (active) setLoadError(userFacingError(cause)); },
    );
    return () => { active = false; };
  }, [id, reload]);

  useEffect(() => {
    if (!id) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || confirmRemove || document.querySelector("[role='menu']")) return;
      if (event.key === "Escape") {
        event.preventDefault();
        if (!busy) onClose();
      }
      if (event.key !== "Tab") return;
      const elements = dialogRef.current?.querySelectorAll<HTMLElement>(
        "button:not([disabled]), a[href], [tabindex='0']",
      );
      if (!elements?.length) return;
      const first = elements[0];
      const last = elements[elements.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault(); last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first?.focus();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [id, confirmRemove, busy, onClose]);

  async function toggle(enabled: boolean) {
    if (!skill || busy) return;
    setError(undefined);
    try {
      await onToggleSkill(skill.qualifiedId, enabled);
      onShowToast(enabled ? "技能已启用，将从下一次任务生效" : "技能已禁用，将从下一次任务生效", "success");
    } catch (cause) { setError(userFacingError(cause)); }
  }

  async function remove() {
    if (!skill || busy) return;
    setError(undefined);
    try {
      const result = await onRemoveSkill(skill.qualifiedId);
      setConfirmRemove(false);
      onClose();
      onShowToast(result.cleanupPending ? "技能已卸载，文件清理已延后" : "技能已卸载", "success");
    } catch (cause) { setError(userFacingError(cause)); }
  }

  async function moreAction(action: "finder" | "copy") {
    if (!currentDetail) return;
    setError(undefined);
    try {
      if (action === "finder") {
        await window.eidosRuntime.showItemInFolder(currentDetail.directory);
      } else {
        await navigator.clipboard.writeText(currentDetail.content);
        onShowToast("Markdown 已复制", "success");
      }
    } catch {
      setError(action === "finder" ? "无法在 Finder 中显示技能目录，请重新打开详情。" : "Markdown 复制失败，请重试。");
    }
  }

  if (!skill) return null;
  return (
    <>
      <div className="modal-backdrop skill-detail-backdrop" inert={confirmRemove} onClick={() => { if (!busy) onClose(); }}>
        <div
          ref={dialogRef}
          className="skill-detail-dialog"
          role="dialog"
          aria-modal="true"
          aria-labelledby="skill-detail-title"
          onClick={(event) => event.stopPropagation()}
        >
          <div className="skill-detail-toolbar">
            <Toggle checked={skill.enabled} disabled={busy || !storageReady} label={`启用或禁用 ${skill.name}`} onChange={(enabled) => void toggle(enabled)} />
            <div className="skill-detail-actions">
              <DropdownMenu trigger="⋯" label="技能选项" triggerAriaLabel="技能选项" items={[
                { key: "finder", label: "在 Finder 中显示", disabled: !currentDetail || busy, onClick: () => void moreAction("finder") },
                { key: "copy", label: "复制 Markdown", disabled: !currentDetail || busy, onClick: () => void moreAction("copy") },
              ]} />
              <Button ref={closeRef} variant="ghost" size="small" aria-label="关闭技能详情" disabled={busy} onClick={onClose}>×</Button>
            </div>
          </div>
          <div className="skill-detail-heading">
            <SkillInitial name={skill.name} />
            <div className="skill-detail-title-row">
              <h2 id="skill-detail-title">{skill.name}</h2>
              <span className="skill-status">{skill.enabled ? "已启用" : "已禁用"}</span>
              <span className="skill-type-label">Skill</span>
            </div>
            <p className="skill-detail-description">{skill.description || "未提供技能说明"}</p>
            {!skill.available && <p className="skill-availability" role="status">所属插件尚未启用。你可以保存技能开关状态，插件启用后才会生效。</p>}
          </div>
          {error && !confirmRemove && <p className="setting-field-error" role="alert">{error}</p>}
          <div className="skill-detail-body" tabIndex={0} aria-label="技能说明" aria-busy={!currentDetail && !loadError}>
            {currentDetail ? (
              <ArtifactProvider value={undefined}><MarkdownContent content={currentDetail.body} /></ArtifactProvider>
            ) : loadError ? (
              <div role="alert"><p>{loadError}</p><Button variant="secondary" size="small" onClick={() => setReload((value) => value + 1)}>重试</Button></div>
            ) : <p role="status">正在读取技能说明…</p>}
          </div>
          {skill.sourceKind !== "system" && (
            <div className="skill-detail-footer">
              <Button variant="danger" size="small" disabled={busy || !storageReady} onClick={() => { setError(undefined); setConfirmRemove(true); }}>卸载</Button>
            </div>
          )}
        </div>
      </div>
      <ConfirmDialog
        open={confirmRemove}
        title={`卸载“${skill.name}”？`}
        description={skill.sourceKind === "plugin"
          ? "该技能将从列表和后续任务中移除。插件包文件会保留，插件的其他技能和 MCP 不受影响。"
          : "该技能将从列表和后续任务中移除，技能目录也会删除。如果任务仍在运行，文件清理会延后。"}
        confirmLabel="卸载"
        isDestructive
        busy={busy}
        error={error}
        getFallbackFocus={getFallbackFocus}
        onConfirm={() => void remove()}
        onCancel={() => { setConfirmRemove(false); setError(undefined); }}
      />
    </>
  );
}
