import { useEffect, useMemo, useState } from "react";
import { parseDiff } from "react-diff-view";
import type { Item, Run } from "../contracts.js";
import { useArtifacts } from "./ArtifactContext.js";
import { Button } from "./Button.js";
import { DropdownMenu, type DropdownMenuItem } from "./DropdownMenu.js";
import { WorkspaceFileIcon } from "./WorkspaceFileIcon.js";

type ChangeState = "committed" | "partial" | "planned";
type ArtifactKind = "docx" | "pdf" | "image" | "html";

export interface TurnTextChange {
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

interface ArtifactEvent {
  path: string;
  kind: ArtifactKind;
  deleted: boolean;
}

const ARTIFACT_EXTENSIONS: Record<string, ArtifactKind> = {
  doc: "docx",
  docx: "docx",
  pdf: "pdf",
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

function artifactKind(path: string): ArtifactKind | undefined {
  const extension = path.toLowerCase().split(".").at(-1) || "";
  return ARTIFACT_EXTENSIONS[extension];
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
    if (kind && kind !== "html" && !deleted) return;
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

function itemArtifactEvents(item: Item): ArtifactEvent[] {
  const call = item.toolCall;
  if (!call) return [];
  const result = parseObject(call.resultJson);
  const data = objectField(result, "data");
  const events = new Map<string, ArtifactEvent>();
  const add = (path: string, deleted = false): void => {
    const normalized = normalizedPath(path);
    const kind = artifactKind(normalized);
    if (!kind) return;
    events.set(normalized, { path: normalized, kind, deleted });
  };
  for (const path of stringArray(objectField(data, "created"))) add(path);
  for (const path of stringArray(objectField(data, "modified"))) add(path);
  for (const path of stringArray(objectField(data, "deleted"))) add(path, true);
  const changes = objectField(data, "changes");
  if (Array.isArray(changes)) {
    for (const change of changes) {
      if (!change || typeof change !== "object") continue;
      const path = typeof Reflect.get(change, "newPath") === "string"
        ? Reflect.get(change, "newPath") as string
        : typeof Reflect.get(change, "path") === "string"
          ? Reflect.get(change, "path") as string
          : undefined;
      if (path) add(path, Reflect.get(change, "kind") === "delete");
    }
  }
  for (const change of parsedDiffChanges(call.changeDiff ?? "")) {
    if (artifactKind(change.path)) add(change.path, change.deleted || call.toolName === "delete_file");
  }
  return [...events.values()];
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
          additions: undefined,
          deletions: undefined,
          state: current.state === "partial" || state === "partial" ? "partial" : state,
          deleted: change.deleted,
          itemId: item.id,
          ...(call.baseSha256 ? { baseSha256: call.baseSha256 } : {}),
        });
      }
    }
    for (const event of itemArtifactEvents(item)) {
      if (event.deleted) {
        artifacts.delete(event.path);
      } else {
        artifacts.set(event.path, { ...event, runId, itemId: item.id });
      }
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
  const latest = new Map<string, TurnArtifact | undefined>();
  for (const item of items) {
    for (const event of itemArtifactEvents(item)) {
      if (event.deleted) latest.set(event.path, undefined);
      else latest.set(event.path, { ...event, runId: item.runId, itemId: item.id });
    }
  }
  return [...latest.values()].filter((artifact): artifact is TurnArtifact => Boolean(artifact));
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
  switch (artifact.kind) {
    case "docx": return "文档 · DOCX";
    case "pdf": return "文档 · PDF";
    case "image": return "图片";
    case "html": return "网页 · HTML";
  }
}

function htmlTitle(content: string | undefined): string | undefined {
  if (!content) return undefined;
  try {
    return new DOMParser().parseFromString(content, "text/html").title.trim() || undefined;
  } catch {
    return undefined;
  }
}

function useHtmlArtifactTitle(artifact: TurnArtifact): string | undefined {
  const actions = useArtifacts();
  const [title, setTitle] = useState<string>();

  useEffect(() => {
    let active = true;
    setTitle(undefined);
    if (
      artifact.kind !== "html"
      || !actions
      || typeof window.eidosRuntime?.readWorkspaceFilePreview !== "function"
    ) return () => { active = false; };

    void window.eidosRuntime.readWorkspaceFilePreview(actions.sessionId, artifact.path)
      .then((preview) => {
        if (active) setTitle(htmlTitle(preview.content) || "未命名网页");
      })
      .catch(() => {
        if (active) setTitle("未命名网页");
      });
    return () => { active = false; };
  }, [actions?.sessionId, artifact.kind, artifact.itemId, artifact.path]);

  return title;
}

function artifactOpenItems(artifact: TurnArtifact, actions: ReturnType<typeof useArtifacts>): DropdownMenuItem[] {
  const items: DropdownMenuItem[] = [];
  if (artifact.kind !== "docx" && actions?.openFile) {
    items.push({
      key: "preview",
      label: "内置预览",
      onClick: () => actions.openFile(artifact.path),
    });
  }
  if (actions?.openExternal) {
    items.push({
      key: "external",
      label: "系统应用打开",
      onClick: () => actions.openExternal?.(artifact.path),
    });
  }
  return items;
}

function ArtifactCard({ artifact, compact = false }: { artifact: TurnArtifact; compact?: boolean }) {
  const actions = useArtifacts();
  const title = useHtmlArtifactTitle(artifact);
  const displayName = artifact.kind === "html" ? (title || "正在读取页面标题…") : fileName(artifact.path);
  const openItems = artifactOpenItems(artifact, actions);
  const open = () => {
    if (artifact.kind === "docx") actions?.openExternal?.(artifact.path);
    else actions?.openFile(artifact.path);
  };
  return (
    <article className={`artifact-result-card${compact ? " artifact-result-card--compact" : ""}`}>
      <button type="button" className="artifact-result-card__main" onClick={open} title={`打开 ${displayName}`} aria-label={`打开 ${displayName}`}>
        <span className="artifact-result-card__icon" aria-hidden="true"><WorkspaceFileIcon name={artifact.path} /></span>
        <span className="artifact-result-card__copy">
          <strong>{displayName}</strong>
          <small>{ArtifactLabel({ artifact })}</small>
        </span>
      </button>
      {!compact && openItems.length > 0 && (
        <DropdownMenu
          trigger="打开方式"
          label={`${fileName(artifact.path)} 的打开方式`}
          className="artifact-result-card__menu"
          items={openItems}
        />
      )}
    </article>
  );
}

function ChangeStats({ additions, deletions }: { additions: number | undefined; deletions: number | undefined }) {
  return additions === undefined || deletions === undefined
    ? <span className="turn-result__stats turn-result__stats--unknown">行数未完整统计</span>
    : <span className="turn-result__stats"><ins>+{additions}</ins><del>-{deletions}</del></span>;
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
          <strong>{title}</strong>
          <ChangeStats additions={projection.additions} deletions={projection.deletions} />
        </div>
        <div className="turn-result-card__actions">
          <Button variant="ghost" size="small" className="turn-result-card__undo" disabled title={undoReason}>撤销</Button>
          <Button
            variant="secondary"
            size="small"
            className="turn-result-card__review"
            disabled={!canReview}
            title={canReview ? "在右侧打开本轮修改" : "当前环境没有可用的 Git 审查面板。"}
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
                <ChangeStats additions={change.additions} deletions={change.deletions} />
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
      {projection.artifacts.map((artifact) => <ArtifactCard artifact={artifact} key={artifact.path} />)}
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
        ? <div className="environment-output__list">{artifacts.map((artifact) => <ArtifactCard artifact={artifact} compact key={artifact.path} />)}</div>
        : <p className="environment-output__empty">当前会话还没有可打开的输出产物。</p>}
    </section>
  );
}
