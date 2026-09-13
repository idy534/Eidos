import { useEffect, useMemo, useRef, useState } from "react";
import { parseDiff } from "react-diff-view";
import { createPortal } from "react-dom";
import type { Item, Run } from "../contracts.js";
import { useArtifacts, usePreviewUrl } from "./ArtifactContext.js";
import { Button } from "./Button.js";
import { DropdownMenu, type DropdownMenuItem } from "./DropdownMenu.js";
import { WorkspaceFileIcon } from "./WorkspaceFileIcon.js";

type ChangeState = "committed" | "partial" | "planned";
type ArtifactKind = "document" | "pdf" | "presentation" | "spreadsheet" | "image" | "html" | "file";

export interface TurnTextChange {
  cumulative?: boolean;
  path: string;
  additions: number | undefined;
  deletions: number | undefined;
  state: ChangeState;
  deleted: boolean;
  runId: string;
  itemId: string;
  baseSha256?: string;
}

export interface TurnArtifact {
  path: string;
  kind: ArtifactKind;
  runId: string;
  itemId: string;
  executionRoot: string;
  version: string;
  sizeBytes: number;
  title?: string;
}

export interface TurnResultProjection {
  runId: string;
  textChanges: TurnTextChange[];
  artifacts: TurnArtifact[];
  additions?: number;
  deletions?: number;
  statsKnown: boolean;
}

interface ParsedFileChange {
  path: string;
  additions?: number;
  deletions?: number;
  deleted: boolean;
}

// Format chooses presentation, never whether a file is a deliverable.
const ARTIFACT_FORMATS: Record<string, ArtifactKind> = {
  doc: "document",
  docx: "document",
  pdf: "pdf",
  ppt: "presentation",
  pptx: "presentation",
  csv: "spreadsheet",
  tsv: "spreadsheet",
  xls: "spreadsheet",
  xlsx: "spreadsheet",
  xlsm: "spreadsheet",
  png: "image",
  jpg: "image",
  jpeg: "image",
  gif: "image",
  webp: "image",
  svg: "image",
  html: "html",
  htm: "html",
};

function parseObject(value: string | undefined): Record<string, unknown> {
  if (!value) return {};
  try {
    const parsed: unknown = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {};
  } catch {
    return {};
  }
}

function objectField(value: unknown, key: string): unknown {
  return value && typeof value === "object" ? Reflect.get(value, key) : undefined;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === "string" && Boolean(entry)) : [];
}

function fileName(path: string): string {
  return path.split("/").at(-1) || path;
}

function fileExtension(path: string): string {
  const name = fileName(path);
  return name.includes(".") ? name.slice(name.lastIndexOf(".") + 1).toLowerCase() : "";
}

function artifactKind(path: string): ArtifactKind | undefined {
  return ARTIFACT_FORMATS[fileExtension(path)];
}

function isOfficeArtifact(path: string): boolean {
  const kind = artifactKind(path);
  return kind === "document" || kind === "presentation"
    || (kind === "spreadsheet" && !["csv", "tsv"].includes(fileExtension(path)));
}

function normalizedPath(path: string): string {
  return path.replace(/^\.\//, "");
}

function parsedDiffChanges(diff: string): ParsedFileChange[] {
  try {
    return parseDiff(diff).flatMap((file) => {
      const path = normalizedPath(file.newPath === "/dev/null" ? file.oldPath : file.newPath);
      if (!path || path === "/dev/null") return [];
      let additions = 0;
      let deletions = 0;
      for (const hunk of file.hunks) {
        for (const change of hunk.changes) {
          if (change.type === "insert") additions += 1;
          if (change.type === "delete") deletions += 1;
        }
      }
      return [{ path, additions, deletions, deleted: file.type === "delete" }];
    });
  } catch {
    return [];
  }
}

function recordedTextChanges(item: Item): ParsedFileChange[] {
  const changes = new Map(parsedDiffChanges(item.toolCall?.changeDiff ?? "").map((change) => [change.path, change]));
  const toolName = item.toolCall?.toolName ?? "";
  if (["run_shell", "write_stdin", "shell"].includes(toolName)) return [...changes.values()];
  const result = parseObject(item.toolCall?.resultJson);
  const data = objectField(result, "data");
  const addRecordedPath = (value: unknown, deleted: boolean): void => {
    if (typeof value !== "string") return;
    const path = normalizedPath(value);
    if (!path || path === "/dev/null") return;
    const kind = artifactKind(path);
    if (!deleted && (kind === "pdf" || kind === "image" || isOfficeArtifact(path))) return;
    const current = changes.get(path);
    if (current) {
      if (deleted) current.deleted = true;
      return;
    }
    changes.set(path, { path, deleted });
  };
  for (const key of ["created", "modified"]) {
    for (const path of stringArray(objectField(data, key))) addRecordedPath(path, false);
  }
  for (const path of stringArray(objectField(data, "deleted"))) addRecordedPath(path, true);
  const resultChanges = objectField(data, "changes");
  if (Array.isArray(resultChanges)) {
    for (const value of resultChanges) {
      if (!value || typeof value !== "object") continue;
      const path = typeof Reflect.get(value, "newPath") === "string"
        ? Reflect.get(value, "newPath")
        : Reflect.get(value, "path");
      const kind = Reflect.get(value, "kind");
      addRecordedPath(path, kind === "delete");
    }
  }
  return [...changes.values()];
}

function changeState(item: Item): ChangeState {
  const result = parseObject(item.toolCall?.resultJson);
  if (item.status === "in_progress" || item.toolCall?.status === "running" || item.toolCall?.approvalStatus === "pending") {
    return "planned";
  }
  if (
    item.status === "completed"
    && item.toolCall?.status === "completed"
    && result.outcome !== "error"
    && result.reconciliationRequired !== true
  ) {
    return "committed";
  }
  return "partial";
}

function itemDeclaredOutputs(item: Item): TurnArtifact[] {
  const call = item.toolCall;
  if (call?.toolName !== "declare_outputs"
    || call.provenance?.kind !== "builtin"
    || call.provenance.sourceId !== "eidos.declare-outputs"
    || item.status !== "completed" || call.status !== "completed") return [];
  const result = parseObject(call.resultJson);
  if (result.outcome !== "success" || result.code !== "ok" || result.reconciliationRequired === true) return [];
  const data = objectField(result, "data");
  const executionRoot = objectField(data, "executionRoot");
  const outputs = objectField(data, "outputs");
  if (typeof executionRoot !== "string" || !executionRoot.startsWith("/")
    || !Array.isArray(outputs) || !outputs.length || outputs.length > 20) return [];
  const artifacts: TurnArtifact[] = [];
  for (const output of outputs) {
    const path = objectField(output, "path");
    const version = objectField(output, "version");
    const sizeBytes = objectField(output, "sizeBytes");
    const title = objectField(output, "title");
    if (typeof path !== "string" || !path || /[\x00-\x1f\x7f\\]/.test(path)
      || path.split("/").some((part) => !part || part === "." || part === "..")
      || typeof version !== "string" || !/^[a-f0-9]{64}$/.test(version)
      || typeof sizeBytes !== "number" || !Number.isSafeInteger(sizeBytes) || sizeBytes < 0
      || (title !== undefined && (typeof title !== "string" || !title.trim() || Array.from(title).length > 120))) return [];
    artifacts.push({
      path, kind: artifactKind(path) ?? "file", executionRoot, version, sizeBytes,
      runId: item.runId, itemId: item.id,
      ...(typeof title === "string" ? { title } : {}),
    });
  }
  return artifacts;
}

function artifactKey(artifact: TurnArtifact): string {
  return JSON.stringify([artifact.executionRoot, artifact.path]);
}

export function projectTurnResults(items: Item[], runId: string): TurnResultProjection {
  const changes = new Map<string, TurnTextChange>();
  const artifacts = new Map<string, TurnArtifact>();

  for (const item of items.filter((candidate) => candidate.runId === runId)) {
    const call = item.toolCall;
    if (!call) continue;
    const state = changeState(item);
    for (const change of recordedTextChanges(item)) {
      const current = changes.get(change.path);
      if (!current) {
        changes.set(change.path, {
          path: change.path,
          additions: change.additions,
          deletions: change.deletions,
          state,
          deleted: change.deleted,
          runId,
          itemId: item.id,
          ...(call.baseSha256 ? { baseSha256: call.baseSha256 } : {}),
        });
      } else {
        changes.set(change.path, {
          ...current,
          additions: current.additions === undefined || change.additions === undefined ? undefined : current.additions + change.additions,
          deletions: current.deletions === undefined || change.deletions === undefined ? undefined : current.deletions + change.deletions,
          cumulative: true,
          state: current.state === "partial" || state === "partial" ? "partial" : state,
          deleted: change.deleted,
          itemId: item.id,
          ...(call.baseSha256 ? { baseSha256: call.baseSha256 } : {}),
        });
      }
    }
    for (const artifact of itemDeclaredOutputs(item)) {
      artifacts.set(artifactKey(artifact), artifact);
    }
  }

  const textChanges = [...changes.values()].sort((a, b) => a.path.localeCompare(b.path));
  const statsKnown = textChanges.every((change) => change.additions !== undefined && change.deletions !== undefined);
  return {
    runId,
    textChanges,
    artifacts: [...artifacts.values()].sort((a, b) => a.path.localeCompare(b.path)),
    ...(statsKnown ? {
      additions: textChanges.reduce((total, change) => total + (change.additions ?? 0), 0),
      deletions: textChanges.reduce((total, change) => total + (change.deletions ?? 0), 0),
    } : {}),
    statsKnown,
  };
}

export function collectOutputArtifacts(items: Item[]): TurnArtifact[] {
  const latest = new Map<string, TurnArtifact>();
  for (const item of items) {
    for (const artifact of itemDeclaredOutputs(item)) {
      latest.set(artifactKey(artifact), artifact);
    }
  }
  return [...latest.values()];
}

export function useCompleteSessionItems(
  sessionId: string | undefined,
  currentItems: Item[],
  previousItemId: string | undefined,
): { items: Item[]; loading: boolean; error?: string } {
  const [olderItems, setOlderItems] = useState<Item[]>([]);
  const [loading, setLoading] = useState(Boolean(previousItemId));
  const [error, setError] = useState<string>();

  useEffect(() => {
    let active = true;
    setOlderItems([]);
    setError(undefined);
    if (!sessionId || !previousItemId) {
      setLoading(false);
      return () => { active = false; };
    }
    setLoading(true);
    void (async () => {
      let cursor: string | undefined = previousItemId;
      const pages: Item[] = [];
      try {
        while (active && cursor) {
          const page = await window.eidosRuntime.readSession(sessionId, { itemLimit: 200, beforeItemId: cursor });
          pages.unshift(...page.items);
          if (!page.previousItemId || page.previousItemId === cursor) break;
          cursor = page.previousItemId;
        }
        if (active) {
          const unique = new Map(pages.map((item) => [item.id, item]));
          setOlderItems([...unique.values()]);
        }
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "更早的结果暂时无法读取。");
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => { active = false; };
  }, [previousItemId, sessionId]);

  const items = useMemo(() => {
    const merged = new Map([...olderItems, ...currentItems].map((item) => [item.id, item]));
    return [...merged.values()];
  }, [currentItems, olderItems]);
  return { items, loading, ...(error ? { error } : {}) };
}

function ArtifactLabel({ artifact }: { artifact: TurnArtifact }): string {
  const format = fileExtension(artifact.path).toUpperCase();
  switch (artifact.kind) {
    case "document": return `文档 · ${format}`;
    case "pdf": return "文档 · PDF";
    case "presentation": return `演示文稿 · ${format}`;
    case "spreadsheet": return `表格 · ${format}`;
    case "image": return `图片 · ${format}`;
    case "html": return `网页 · ${format}`;
    case "file": return `文件${format ? ` · ${format}` : ""}`;
  }
}

function artifactOpenItems(
  artifact: TurnArtifact,
  actions: ReturnType<typeof useArtifacts>,
  openBuiltInPreview: () => void,
  openExternal: () => void,
): DropdownMenuItem[] {
  const items: DropdownMenuItem[] = [];
  if (!isOfficeArtifact(artifact.path) && actions?.openFile) {
    items.push({
      key: "preview",
      label: artifact.kind === "file" ? "打开文件" : artifact.kind === "spreadsheet" ? "文本预览" : "内置预览",
      onClick: openBuiltInPreview,
    });
  }
  if (actions?.openExternal) {
    items.push({
      key: "external",
      label: "系统应用打开",
      onClick: openExternal,
    });
  }
  return items;
}

function ArtifactCard({ artifact, compact = false }: { artifact: TurnArtifact; compact?: boolean }) {
  const actions = useArtifacts();
  const displayName = artifact.title || fileName(artifact.path);
  const [opening, setOpening] = useState(false);
  const [openError, setOpenError] = useState<string>();
  const [versionNote, setVersionNote] = useState<string>();
  const [previewVersion, setPreviewVersion] = useState<string>();
  const openSequence = useRef(0);
  const wrongWorkspace = Boolean(actions && artifact.executionRoot !== actions.executionRoot);
  const [imageOpen, setImageOpen] = useState(false);
  const [htmlOpenPending, setHtmlOpenPending] = useState(false);
  const [htmlOpenHandled, setHtmlOpenHandled] = useState(false);
  const previewPath = !wrongWorkspace && (imageOpen || htmlOpenPending)
    ? artifact.path
    : undefined;
  const { url: previewUrl, error: previewError } = usePreviewUrl(previewPath, previewVersion);

  useEffect(() => {
    setOpening(false);
    setOpenError(undefined);
    setVersionNote(undefined);
    setImageOpen(false);
    setHtmlOpenPending(false);
    return () => { openSequence.current += 1; };
  }, [actions?.sessionId, actions?.executionRoot, artifact.itemId, artifact.version]);

  useEffect(() => {
    if (!htmlOpenPending || htmlOpenHandled || wrongWorkspace || !actions) return;
    if (previewUrl) {
      setHtmlOpenHandled(true);
      actions.openBrowser(previewUrl);
    } else if (previewError) {
      setOpenError(previewError);
      setHtmlOpenPending(false);
    }
  }, [actions, htmlOpenHandled, htmlOpenPending, previewError, previewUrl, wrongWorkspace]);

  useEffect(() => {
    if (!imageOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setImageOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [imageOpen]);

  const open = async (external = false) => {
    if (!actions || wrongWorkspace || opening) return;
    const sequence = ++openSequence.current;
    setOpening(true);
    setOpenError(undefined);
    setHtmlOpenPending(false);
    try {
      const preview = await window.eidosRuntime.readWorkspaceFilePreview(actions.sessionId, artifact.path);
      if (sequence !== openSequence.current) return;
      setVersionNote(preview.version && preview.version !== artifact.version
        ? "文件已在声明后变化，打开的是当前版本。" : undefined);
      setPreviewVersion(preview.version);
      if (external || isOfficeArtifact(artifact.path) || preview.kind === "unavailable") {
        if (!actions.openExternal) throw new Error("当前没有系统应用打开入口。");
        await actions.openExternal(artifact.path);
      } else if (preview.kind === "image") {
        setImageOpen(true);
      } else if (preview.kind === "html") {
        setHtmlOpenHandled(false);
        setHtmlOpenPending(true);
      } else {
        actions.openFile(artifact.path);
      }
    } catch (error) {
      if (sequence === openSequence.current) setOpenError(
        `无法打开文件。${error instanceof Error ? error.message : "文件可能已删除或无法访问。"}`,
      );
    } finally {
      if (sequence === openSequence.current) setOpening(false);
    }
  };
  const openItems = artifactOpenItems(artifact, actions, () => { void open(); }, () => { void open(true); });
  return (
    <>
      <article className={`artifact-result-card${compact ? " artifact-result-card--compact" : ""}`}>
        <button type="button" className="artifact-result-card__main" disabled={!actions || wrongWorkspace || opening} onClick={() => { void open(); }} title={artifact.path} aria-label={`打开 ${displayName}`}>
          <span className="artifact-result-card__icon" aria-hidden="true"><WorkspaceFileIcon name={artifact.path} /></span>
          <span className="artifact-result-card__copy">
            <strong>{displayName}</strong>
            <small>{ArtifactLabel({ artifact })}{artifact.title ? ` · ${fileName(artifact.path)}` : ""}</small>
          </span>
        </button>
        {!compact && !wrongWorkspace && !opening && openItems.length > 0 && (
          <DropdownMenu
            trigger="打开方式"
            label={`${fileName(artifact.path)} 的打开方式`}
            className="artifact-result-card__menu"
            items={openItems}
          />
        )}
      </article>
      {(wrongWorkspace || openError || versionNote) && <p className="turn-result-card__note" role={openError ? "alert" : "status"}>
        {wrongWorkspace ? "此产物属于其他执行目录，当前工作区无法打开。" : openError || versionNote}
      </p>}
      {imageOpen && typeof document !== "undefined" && createPortal(
        <div
          className="artifact-image-preview"
          role="dialog"
          aria-modal="true"
          aria-label={`${displayName} 图片预览`}
          onClick={(event) => {
            if (event.target === event.currentTarget) setImageOpen(false);
          }}
        >
          <button type="button" className="artifact-image-preview__close" aria-label="关闭图片预览" autoFocus onClick={() => setImageOpen(false)}>×</button>
          {previewUrl ? <img src={previewUrl} alt={displayName} /> : <p role={previewError ? "alert" : "status"}>{previewError || "正在读取图片预览…"}</p>}
        </div>,
        document.body,
      )}
    </>
  );
}

function ChangeStats({ additions, deletions, cumulative = false }: { additions: number | undefined; deletions: number | undefined; cumulative?: boolean }) {
  return additions === undefined || deletions === undefined
    ? <span className="turn-result__stats turn-result__stats--unknown">行数未完整统计</span>
    : <span className="turn-result__stats" title={cumulative ? "本轮各次补丁的累计增删，包含重复编辑，不是最终净差异。" : undefined}>{cumulative && <span className="turn-result__stats-label">累计</span>}<ins>+{additions}</ins><del>-{deletions}</del></span>;
}

function ChangePath({ path }: { path: string }) {
  const name = fileName(path);
  const directory = path.slice(0, Math.max(0, path.length - name.length));
  return <span className="turn-result__path"><span className="turn-result__directory">{directory}</span><strong>{name}</strong></span>;
}

function TextChangeCard({ projection }: { projection: TurnResultProjection }) {
  const actions = useArtifacts();
  const [expanded, setExpanded] = useState(false);
  const files = expanded ? projection.textChanges : projection.textChanges.slice(0, 3);
  const hidden = projection.textChanges.length - files.length;
  const first = projection.textChanges[0];
  if (!first) return null;
  const title = projection.textChanges.length === 1 ? `已编辑 ${fileName(first.path)}` : `已编辑 ${projection.textChanges.length} 个文件`;
  const undoReason = "当前会话没有可精确恢复本轮修改的检查点。";
  const review = (change?: TurnTextChange) => {
    if (!actions?.openReview) return;
    actions.openReview({ runId: projection.runId, ...(change ? { path: change.path, itemId: change.itemId } : {}) });
  };
  const canReview = Boolean(actions?.openReview);
  return (
    <article className="turn-result-card turn-result-card--changes">
      <header className="turn-result-card__header">
        <span className="turn-result-card__icon" aria-hidden="true"><WorkspaceFileIcon name={first.path} /></span>
        <div className="turn-result-card__title">
          <button type="button" className="turn-result-card__title-link" disabled={!canReview} onClick={() => review(projection.textChanges.length === 1 ? first : undefined)} title="打开文本修改审查"><strong>{title}</strong></button>
          <ChangeStats additions={projection.additions} deletions={projection.deletions} cumulative={projection.textChanges.some((change) => change.cumulative)} />
        </div>
        <div className="turn-result-card__actions">
          <Button variant="ghost" size="small" className="turn-result-card__undo" disabled title={undoReason}>撤销</Button>
          <Button
            variant="secondary"
            size="small"
            className="turn-result-card__review"
            disabled={!canReview}
            title={canReview ? "在右侧打开本轮修改" : "当前会话没有可用的文本审查面板。"}
            onClick={() => review()}
          >
            审核
          </Button>
        </div>
      </header>
      {projection.textChanges.length > 1 && (
        <>
          <div className="turn-result-card__files">
            {files.map((change) => (
              <button type="button" className="turn-result-file" key={`${change.path}:${change.itemId}`} onClick={() => review(change)} title={`审核 ${change.path}`} aria-label={`审核 ${change.path}`}>
                <ChangePath path={change.path} />
                <ChangeStats additions={change.additions} deletions={change.deletions} cumulative={change.cumulative === true} />
              </button>
            ))}
          </div>
          {(hidden > 0 || expanded) && (
            <button type="button" className="turn-result-card__more" onClick={() => setExpanded((value) => !value)}>
              {expanded ? "收起文件" : `再显示 ${hidden} 个文件`}
            </button>
          )}
        </>
      )}
      {projection.textChanges.some((change) => change.state !== "committed") && (
        <p className="turn-result-card__note">本轮包含未完整提交或待核验的文件修改。</p>
      )}
    </article>
  );
}

export function TurnResults({ run, items, showTextChanges = true }: { run: Run; items: Item[]; showTextChanges?: boolean }) {
  const projection = useMemo(() => projectTurnResults(items, run.id), [items, run.id]);
  if (!projection.artifacts.length && (!showTextChanges || !projection.textChanges.length)) return null;
  return (
    <section className="turn-results" aria-label="本轮结果">
      {projection.artifacts.map((artifact) => <ArtifactCard artifact={artifact} key={artifactKey(artifact)} />)}
      {showTextChanges && projection.textChanges.length > 0 && <TextChangeCard projection={projection} />}
    </section>
  );
}

export function OutputContent({ artifacts, loading = false, error }: {
  artifacts: TurnArtifact[];
  loading?: boolean;
  error?: string | undefined;
}) {
  return (
    <section className="environment-output" aria-label="输出内容">
      <header className="environment-output__header"><h2>输出内容</h2><span>{artifacts.length || ""}</span></header>
      {loading && <p className="environment-output__status" role="status">正在读取更早的输出…</p>}
      {error && <p className="environment-output__status" role="alert">更早的输出暂时无法读取：{error}</p>}
      {artifacts.length > 0
        ? <div className="environment-output__list">{artifacts.map((artifact) => <ArtifactCard artifact={artifact} compact key={artifactKey(artifact)} />)}</div>
        : <p className="environment-output__empty">当前会话还没有已声明的输出产物。</p>}
    </section>
  );
}
