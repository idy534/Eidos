import { useEffect, useRef, useState } from "react";
import type { SkillDetail, SkillMetadata, SkillRemoval } from "../../contracts.js";
import { userFacingError } from "../../session-state.js";
import { ArtifactProvider } from "../ArtifactContext.js";
import { Button } from "../Button.js";
import { DropdownMenu } from "../DropdownMenu.js";
import { FinderIcon, CopyIcon } from "../MenuIcons.js";
import { MarkdownContent } from "../MarkdownContent.js";
import { useDialogFocusLifecycle } from "../useDialogFocusLifecycle.js";
import { ConfirmDialog } from "../ConfirmDialog.js";
import { Toggle } from "../Toggle.js";
import type { SettingsPendingAction } from "./settings-types.js";
import { githubIcon } from "./SettingsIcons.js";

export function SkillInitial({ name }: { name: string }) {
  return <span className="skill-initial" aria-hidden="true">{Array.from(name.trim())[0]?.toLocaleUpperCase() ?? "S"}</span>;
}

export function SkillIcon({ name, icon }: { name: string; icon?: string | null | undefined }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setFailed(false);
  }, [icon]);
  const isGitHub = name.trim().toLowerCase() === "github";
  const effectiveIcon = !failed && icon ? icon : isGitHub ? githubIcon : undefined;
  if (effectiveIcon) {
    return (
      <span className="skill-icon" aria-hidden="true">
        <img src={effectiveIcon} alt={name} onError={() => setFailed(true)} />
      </span>
    );
  }
  return <SkillInitial name={name} />;
}

export interface SkillDetailDialogProps {
  skill: SkillMetadata | {
    qualifiedId: string;
    name: string;
    description?: string | undefined;
    sourceKind?: "system" | "user" | "plugin" | undefined;
    enabled?: boolean | undefined;
    available?: boolean | undefined;
    icon?: string | null | undefined;
  } | undefined;
  pendingAction?: SettingsPendingAction | undefined;
  pendingSkillIds?: ReadonlySet<string> | undefined;
  storageReady?: boolean | undefined;
  onToggleSkill?: ((id: string, enabled: boolean) => Promise<void>) | undefined;
  onRemoveSkill?: ((id: string) => Promise<SkillRemoval>) | undefined;
  onShowToast?: ((message: string, type?: "success" | "info" | "error") => void) | undefined;
  onClose: () => void;
  getFallbackFocus?: (() => HTMLElement | null) | undefined;
  readOnly?: boolean | undefined;
}

export function extractFrontmatterDescription(content?: string): string | undefined {
  if (!content) return undefined;
  const match = content.match(/^---\r?\n([\s\S]*?)\r?\n(?:---|\.\.\.)/);
  if (!match || !match[1]) return undefined;
  const frontmatter = match[1];
  const lines = frontmatter.split(/\r?\n/);
  let inDesc = false;
  let isFolded = false;
  const descLines: string[] = [];
  for (const line of lines) {
    if (!inDesc) {
      const descMatch = line.match(/^description:\s*(.*)$/);
      if (descMatch && descMatch[1] !== undefined) {
        const rest = descMatch[1].trim();
        if (rest === ">" || rest === "|") {
          inDesc = true;
          isFolded = true;
        } else if (rest) {
          let val = rest;
          if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
            val = val.slice(1, -1);
          }
          return val.trim();
        } else {
          inDesc = true;
        }
      }
    } else {
      if (/^\s+/.test(line)) {
        descLines.push(line.trim());
      } else {
        break;
      }
    }
  }
  if (descLines.length > 0) {
    return descLines.join(isFolded ? " " : "\n").trim();
  }
  return undefined;
}

export function SkillDetailDialog({
  skill,
  pendingAction,
  pendingSkillIds,
  storageReady = true,
  onToggleSkill,
  onRemoveSkill,
  onShowToast = () => {},
  onClose,
  getFallbackFocus = () => null,
  readOnly = false,
}: SkillDetailDialogProps) {
  const [detail, setDetail] = useState<SkillDetail>();
  const [matchedMetadata, setMatchedMetadata] = useState<SkillMetadata>();
  const [icon, setIcon] = useState<string | null | undefined>(skill?.icon);
  const [error, setError] = useState<string>();
  const [loadError, setLoadError] = useState<string>();
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [reload, setReload] = useState(0);
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const id = skill?.qualifiedId;
  const effectiveId = matchedMetadata?.qualifiedId || id;
  const isPending =
    effectiveId !== undefined &&
    (pendingSkillIds?.has(effectiveId) ||
      (pendingAction?.type === "toggle_skill" && pendingAction.qualifiedId === effectiveId) ||
      (pendingAction?.type === "remove_skill" && pendingAction.qualifiedId === effectiveId));
  const busy = isPending;
  const currentDetail =
    detail && (detail.qualifiedId === id || (effectiveId && detail.qualifiedId === effectiveId))
      ? detail
      : undefined;
  useDialogFocusLifecycle({ open: Boolean(skill), initialFocusRef: closeRef, getFallbackFocus });

  useEffect(() => {
    setIcon(skill?.icon);
    setMatchedMetadata(undefined);
    if (!id) return;
    if (typeof window !== "undefined" && window.eidosRuntime?.listSkills) {
      let active = true;
      void window.eidosRuntime.listSkills().then(
        (result) => {
          if (!active) return;
          const match = result?.skills?.find(
            (s) => s.qualifiedId === id || s.name === id || s.qualifiedId.endsWith(`:${id}`)
          );
          if (match) {
            setMatchedMetadata(match);
            if (!skill?.icon && match.icon) {
              setIcon(match.icon);
            }
          }
        },
        () => {},
      );
      return () => {
        active = false;
      };
    }
  }, [id, skill?.icon]);

  useEffect(() => {
    setDetail(undefined);
    setError(undefined);
    setLoadError(undefined);
    setConfirmRemove(false);
    const targetId = effectiveId || id;
    if (!targetId) return;
    let active = true;
    if (typeof window !== "undefined" && window.eidosRuntime?.readSkillDetail) {
      void window.eidosRuntime.readSkillDetail(targetId).then(
        (result) => { if (active) setDetail(result); },
        (cause: unknown) => { if (active) setLoadError(userFacingError(cause)); },
      );
    }
    return () => { active = false; };
  }, [effectiveId, id, reload]);

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
        "button:not([disabled]), input:not([disabled]), a[href], [tabindex='0']",
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
    const targetId = effectiveId || skill?.qualifiedId;
    if (!targetId || busy || !onToggleSkill) return;
    setError(undefined);
    try {
      await onToggleSkill(targetId, enabled);
      onShowToast(enabled ? "技能已启用，将从下一次任务生效" : "技能已禁用，将从下一次任务生效", "success");
    } catch (cause) {
      const msg = userFacingError(cause);
      setError(msg);
      onShowToast(msg, "error");
    }
  }

  async function remove() {
    const targetId = effectiveId || skill?.qualifiedId;
    if (!targetId || busy || !onRemoveSkill) return;
    setError(undefined);
    try {
      const result = await onRemoveSkill(targetId);
      setConfirmRemove(false);
      onClose();
      onShowToast(result.cleanupPending ? "技能已卸载，文件清理已延后" : "技能已卸载", "success");
    } catch (cause) {
      const msg = userFacingError(cause);
      setError(msg);
      onShowToast(msg, "error");
    }
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
      const msg = action === "finder" ? "无法在 Finder 中显示技能目录，请重新打开详情。" : "Markdown 复制失败，请重试。";
      setError(msg);
      onShowToast(msg, "error");
    }
  }

  if (!skill) return null;
  const effectiveName = skill.name || matchedMetadata?.name || id || "";
  const effectiveIcon = icon ?? skill.icon ?? matchedMetadata?.icon;
  const effectiveDescription =
    skill.description
    || matchedMetadata?.description
    || (currentDetail as { description?: string } | undefined)?.description
    || extractFrontmatterDescription(currentDetail?.content);
  const effectiveAvailable = skill.available ?? matchedMetadata?.available ?? true;
  const effectiveSourceKind = skill.sourceKind ?? matchedMetadata?.sourceKind;
  const effectiveEnabled = skill.enabled ?? matchedMetadata?.enabled ?? true;

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
          <div className="skill-detail-header">
            <div className="skill-detail-header-left">
              <SkillIcon name={effectiveName} icon={effectiveIcon} />
              <div className="skill-detail-title-group">
                <h2 id="skill-detail-title">{effectiveName}</h2>
                <span className="skill-type-label">Skill</span>
              </div>
            </div>
            <div className="skill-detail-header-right">
              {!readOnly && onToggleSkill && (
                <Toggle
                  checked={Boolean(effectiveEnabled)}
                  disabled={busy || !storageReady}
                  label={`启用或禁用 ${effectiveName}`}
                  onChange={(enabled) => void toggle(enabled)}
                />
              )}
              <div className="skill-detail-actions">
                <DropdownMenu
                  trigger="⋯"
                  label="技能选项"
                  triggerAriaLabel="技能选项"
                  className="skill-detail-menu"
                  items={[
                    { key: "finder", label: "在 Finder 中显示", icon: <FinderIcon />, disabled: !currentDetail || busy, onClick: () => void moreAction("finder") },
                    { key: "copy", label: "复制 Markdown", icon: <CopyIcon />, disabled: !currentDetail || busy, onClick: () => void moreAction("copy") },
                  ]}
                />
                <button
                  ref={closeRef}
                  type="button"
                  className="skill-detail-close-btn"
                  aria-label="关闭技能详情"
                  disabled={busy}
                  onClick={onClose}
                >
                  ×
                </button>
              </div>
            </div>
          </div>
          <p className="skill-detail-description">{effectiveDescription || "未提供技能说明"}</p>
          {!effectiveAvailable && <p className="skill-availability" role="status">所属插件尚未启用。你可以保存技能开关状态，插件启用后才会生效。</p>}
          <div className="skill-detail-body" tabIndex={0} aria-label="技能说明" aria-busy={!currentDetail && !loadError}>
            {currentDetail ? (
              <ArtifactProvider value={undefined}><MarkdownContent content={currentDetail.body} /></ArtifactProvider>
            ) : loadError ? (
              <div role="alert"><p>{loadError}</p><Button variant="secondary" size="small" onClick={() => setReload((value) => value + 1)}>重试</Button></div>
            ) : <p role="status">正在读取技能说明…</p>}
          </div>
          {!readOnly && effectiveSourceKind !== "system" && onRemoveSkill && (
            <div className="skill-detail-footer">
              <Button variant="danger" size="small" disabled={busy || !storageReady} onClick={() => { setError(undefined); setConfirmRemove(true); }}>卸载</Button>
            </div>
          )}
        </div>
      </div>
      {!readOnly && onRemoveSkill && (
        <ConfirmDialog
          open={confirmRemove}
          title={`卸载“${effectiveName}”？`}
          description={effectiveSourceKind === "plugin"
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
      )}
    </>
  );
}

