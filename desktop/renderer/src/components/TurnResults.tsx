import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { parseDiff } from "react-diff-view";
import { createPortal } from "react-dom";
import Markdown from "react-markdown";
import type { Item, Run } from "../contracts.js";
import { artifactPath, useArtifacts, usePreviewUrl } from "./ArtifactContext.js";
import { toolFileChanges } from "./ResultFiles.js";
import { userFacingError } from "../session-state.js";
import type { WorkspaceFilePreview } from "../contracts.js";
import { Button } from "./Button.js";
import { DropdownMenu, type DropdownMenuItem } from "./DropdownMenu.js";
import { WorkspaceFileIcon } from "./WorkspaceFileIcon.js";

type ChangeState = "committed" | "partial" | "planned";
type ArtifactKind = "docx" | "pdf" | "image" | "html" | "file";

export interface TurnTextChange {
  cumulative?: boolean;
  missingPatch?: boolean;
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
  state?: ChangeState | "referenced";
  observed?: boolean;
  deleted?: boolean;
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
  unknownFiles: number;
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
  observed: boolean;
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


function fileName(path: string): string {
  return path.split("/").at(-1) || path;
}

function artifactKind(path: string): ArtifactKind {
  const extension = path.toLowerCase().split(".").at(-1) || "";
  return ARTIFACT_EXTENSIONS[extension] ?? "file";
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
  const call = item.toolCall;
  if (!call) return [];
  const changes = new Map(parsedDiffChanges(call.changeDiff ?? "").map((change) => [change.path, change]));
  for (const event of toolFileChanges(call)) {
    const path = normalizedPath(event.path);
    // Without a text patch, a path alone does not establish that a file is text.
    if (!changes.has(path) && /\.(?:txt|md|markdown|mdx|py|ts|tsx|js|jsx|json|css|html|htm|go|rs|java|c|h|cpp|sh|yaml|yml|toml|xml|csv)$/i.test(path)) {
      changes.set(path, { path, deleted: event.deleted });
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

function itemArtifactEvents(item: Item, root?: string): ArtifactEvent[] {
  if (!item.toolCall) return [];
  return toolFileChanges(item.toolCall).flatMap((event) => {
    const path = root ? artifactPath(event.path, root) : normalizedPath(event.path);
    return path ? [{ ...event, path, kind: artifactKind(path) }] : [];
  });
}

export function projectTurnResults(items: Item[], runId: string, root?: string): TurnResultProjection {
  const changes = new Map<string, TurnTextChange>();
  const artifacts = new Map<string, TurnArtifact>();

  for (const item of items.filter((candidate) => candidate.runId === runId).sort((a, b) => a.ordinal - b.ordinal)) {
    const call = item.toolCall;
    if (!call) continue;
    const state = changeState(item);
    for (const recorded of recordedTextChanges(item)) {
      const path = root ? artifactPath(recorded.path, root) : recorded.path;
      if (!path) continue;
      const change = { ...recorded, path };
      const current = changes.get(change.path);
      if (!current) {
        changes.set(change.path, {
          path: change.path,
          additions: change.additions,
          deletions: change.deletions,
          missingPatch: change.additions === undefined || change.deletions === undefined,
          state,
          deleted: change.deleted,
          runId,
          itemId: item.id,
          ...(call.baseSha256 ? { baseSha256: call.baseSha256 } : {}),
        });
      } else {
        changes.set(change.path, {
          ...current,
          additions: current.additions === undefined && change.additions === undefined ? undefined : (current.additions ?? 0) + (change.additions ?? 0),
          deletions: current.deletions === undefined && change.deletions === undefined ? undefined : (current.deletions ?? 0) + (change.deletions ?? 0),
          missingPatch: current.missingPatch || change.additions === undefined || change.deletions === undefined,
          cumulative: true,
          state: current.state === "partial" || state === "partial" ? "partial" : state,
          deleted: change.deleted,
          itemId: item.id,
          ...(call.baseSha256 ? { baseSha256: call.baseSha256 } : {}),
        });
      }
    }
    for (const event of itemArtifactEvents(item, root)) {
      if (event.deleted && state === "committed") {
        artifacts.delete(event.path);
      } else {
        artifacts.set(event.path, { ...event, runId, itemId: item.id, state });
      }
    }
  }

  const textChanges = [...changes.values()].sort((a, b) => a.path.localeCompare(b.path));
  const statsKnown = textChanges.every((change) => !change.missingPatch);
  return {
    runId,
    textChanges,
    artifacts: [...artifacts.values()].sort((a, b) => a.path.localeCompare(b.path)),
    unknownFiles: textChanges.filter((change) => change.missingPatch).length,
    ...(textChanges.some((change) => change.additions !== undefined) ? {
      additions: textChanges.reduce((total, change) => total + (change.additions ?? 0), 0),
      deletions: textChanges.reduce((total, change) => total + (change.deletions ?? 0), 0),
    } : {}),
    statsKnown,
  };
}

export function collectOutputArtifacts(items: Item[], root?: string): TurnArtifact[] {
  const latest = new Map<string, TurnArtifact | undefined>();
  for (const item of [...items].sort((a, b) => a.ordinal - b.ordinal)) {
    for (const event of itemArtifactEvents(item, root)) {
      if (event.deleted && changeState(item) === "committed") latest.set(event.path, undefined);
      else latest.set(event.path, { ...event, runId: item.runId, itemId: item.id, state: changeState(item) });
    }
  }
  return [...latest.values()].filter((artifact): artifact is TurnArtifact => Boolean(artifact));
}

export function useCompleteSessionItems(
  sessionId: string | undefined,
  currentItems: Item[],
  previousItemId: string | undefined,
): { items: Item[]; loading: boolean; error?: string; hasMore: boolean; loadMore(): void } {
  const [history, setHistory] = useState<{ sessionId?: string | undefined; anchor?: string | undefined; items: Item[]; cursor?: string | undefined; loading: boolean; error?: string | undefined }>({ items: [], loading: false });
  const historyRef = useRef(history);
  historyRef.current = history;
  const generation = useRef(0);
  const inFlight = useRef(false);
  const loadPage = useCallback(async (cursor: string, reset: boolean) => {
    if (!sessionId || inFlight.current) return;
    const request = generation.current;
    inFlight.current = true;
    setHistory((current) => ({ sessionId, anchor: previousItemId, items: reset ? [] : current.items, cursor, loading: true }));
    try {
      const page = await window.eidosRuntime.readSession(sessionId, { itemLimit: 200, beforeItemId: cursor });
      if (generation.current !== request) return;
      setHistory((current) => ({ ...current, items: [...page.items, ...current.items], cursor: page.previousItemId === cursor ? undefined : page.previousItemId, loading: false }));
    } catch (cause) {
      if (generation.current === request) setHistory((current) => ({ ...current, loading: false, error: userFacingError(cause) }));
    } finally {
      if (generation.current === request) inFlight.current = false;
    }
  }, [sessionId, previousItemId]);
  useEffect(() => {
    generation.current += 1;
    inFlight.current = false;
    setHistory({ sessionId, anchor: previousItemId, items: [], cursor: previousItemId, loading: false });
    if (previousItemId) void loadPage(previousItemId, true);
    return () => { generation.current += 1; inFlight.current = false; };
  }, [sessionId, previousItemId, loadPage]);
  const active = history.sessionId === sessionId && history.anchor === previousItemId;
  const items = useMemo(() => [...new Map([...(active ? history.items : []), ...currentItems].map((item) => [item.id, item])).values()].sort((a, b) => a.ordinal - b.ordinal), [active, history.items, currentItems]);
  return {
    items, loading: active && history.loading, hasMore: Boolean(active ? history.cursor : previousItemId),
    ...(active && history.error ? { error: history.error } : {}),
    loadMore: () => { const cursor = historyRef.current.cursor; if (cursor) void loadPage(cursor, false); },
  };
}

function ArtifactLabel({ artifact }: { artifact: TurnArtifact }): string {
  switch (artifact.kind) {
    case "docx": return artifact.path.toLowerCase().endsWith(".doc") ? "文档 · DOC" : "文档 · DOCX";
    case "pdf": return "文档 · PDF";
    case "image": return "图片";
    case "html": return "网页 · HTML";
    case "file": return `文件 · ${fileName(artifact.path).split(".").slice(1).at(-1)?.toUpperCase() || "未知格式"}`;
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

function useArtifactMetadata(artifact: TurnArtifact) {
  const actions = useArtifacts();
  const [revision, setRevision] = useState(0);
  const [metadata, setMetadata] = useState<{ key: string; preview?: WorkspaceFilePreview; error?: string }>({ key: "" });
  const key = `${actions?.sessionId}:${actions?.executionRoot}:${artifact.path}:${artifact.itemId}:${revision}`;
  useEffect(() => {
    let active = true;
    if (!actions || typeof window.eidosRuntime?.readWorkspaceFilePreview !== "function") return;
    void window.eidosRuntime.readWorkspaceFilePreview(actions.sessionId, artifact.path)
      .then((preview) => { if (active) setMetadata({ key, preview }); })
      .catch((cause) => { if (active) setMetadata({ key, error: userFacingError(cause) }); });
    return () => { active = false; };
  }, [key]);
  useEffect(() => {
    if (!actions || typeof window.eidosRuntime?.onNotification !== "function") return;
    return window.eidosRuntime.onNotification((event) => {
      if (event.method === "workspace/changed" && event.params.sessionId === actions.sessionId
        && event.params.paths.some((path) => path === "." || path === artifact.path || artifact.path.startsWith(path + "/"))) setRevision((value) => value + 1);
    });
  }, [actions?.sessionId, artifact.path]);
  return { ...(metadata.key === key ? metadata : { key }), refresh: () => setRevision((value) => value + 1) };
}

function artifactOpenItems(
  artifact: TurnArtifact,
  actions: ReturnType<typeof useArtifacts>,
  openBuiltInPreview: () => void,
  canPreview: boolean,
): DropdownMenuItem[] {
  const items: DropdownMenuItem[] = [];
  if (canPreview && actions?.openFile) {
    items.push({
      key: "preview",
      label: "内置预览",
      onClick: openBuiltInPreview,
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
  const { preview, error: metadataError, refresh } = useArtifactMetadata(artifact);
  const displayName = artifact.kind === "html" ? (htmlTitle(preview?.content) || fileName(artifact.path)) : fileName(artifact.path);
  const canPreview = Boolean(preview && preview.kind !== "unavailable");
  const [imageOpen, setImageOpen] = useState(false);
  const [htmlOpenPending, setHtmlOpenPending] = useState(false);
  const [htmlOpenHandled, setHtmlOpenHandled] = useState(false);
  const previewPath = (artifact.kind === "image" && imageOpen) || (artifact.kind === "html" && htmlOpenPending)
    ? artifact.path
    : undefined;
  const { url: previewUrl, error: previewError } = usePreviewUrl(previewPath, preview?.version);

  useEffect(() => {
    if (!htmlOpenPending || htmlOpenHandled || artifact.kind !== "html" || !actions) return;
    if (previewUrl) {
      setHtmlOpenHandled(true);
      actions.openBrowser(previewUrl);
    } else if (previewError) {
      setHtmlOpenPending(false);
    }
  }, [actions, artifact.kind, htmlOpenPending, previewError, previewUrl]);

  useEffect(() => {
    if (!imageOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setImageOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [imageOpen]);

  const openBuiltInPreview = () => {
    if (artifact.kind === "image") {
      setImageOpen(true);
    } else if (artifact.kind === "html") {
      setHtmlOpenHandled(false);
      setHtmlOpenPending(true);
    } else {
      actions?.openFile(artifact.path);
    }
  };
  const openItems = artifactOpenItems(artifact, actions, openBuiltInPreview, canPreview);
  openItems.push({ key: "refresh", label: "刷新文件状态", onClick: refresh });
  const open = () => {
    if (canPreview) openBuiltInPreview();
    else actions?.openFile(artifact.path);
  };
  return (
    <>
      <article className={`artifact-result-card${compact ? " artifact-result-card--compact" : ""}`}>
        <button type="button" className="artifact-result-card__main" onClick={open} title={`打开当前文件：${artifact.path}`} aria-label={`打开 ${displayName}`}>
          <span className="artifact-result-card__icon" aria-hidden="true"><WorkspaceFileIcon name={artifact.path} /></span>
          <span className="artifact-result-card__copy">
            <strong>{displayName}</strong>
            <small>{ArtifactLabel({ artifact })} · {artifact.state === "referenced" ? "回复引用，未证明生成" : artifact.state === "planned" ? "准备或执行中" : artifact.state === "committed" ? "工具已完成" : "未完整完成或待核验"}{artifact.deleted ? " · 删除待核验" : ""}</small>
            <small>{metadataError || (preview ? `当前文件 · ${preview.sizeBytes.toLocaleString()} 字节${canPreview ? "" : " · 暂无内置预览"}` : "正在核对当前文件…")}</small>
            {artifact.observed && <small>工作区观察记录，不能确认由单个命令独占生成</small>}
          </span>
        </button>
        {openItems.length > 0 && (
          <DropdownMenu
            trigger="打开方式"
            label={`${fileName(artifact.path)} 的打开方式`}
            className="artifact-result-card__menu"
            items={openItems}
          />
        )}
      </article>
      {previewError && <p role="alert">{previewError}<button type="button" onClick={refresh}>重新读取</button></p>}
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

function ChangeStats({ additions, deletions, cumulative = false, unknownFiles = 0 }: { additions: number | undefined; deletions: number | undefined; cumulative?: boolean; unknownFiles?: number }) {
  return additions === undefined || deletions === undefined
    ? <span className="turn-result__stats turn-result__stats--unknown">缺少可统计的文本补丁</span>
    : <span className="turn-result__stats" title={cumulative ? "本轮各次补丁的累计增删，包含重复编辑，不是最终净差异。" : undefined}>{cumulative && <span className="turn-result__stats-label">累计</span>}<ins>+{additions}</ins><del>-{deletions}</del>{unknownFiles > 0 && <span>（已知补丁，另有 {unknownFiles} 个文件缺少统计）</span>}</span>;
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
  const title = projection.textChanges.length === 1 ? `文件修改 · ${fileName(first.path)}` : `文件修改 · ${projection.textChanges.length} 个文件`;
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
          <ChangeStats additions={projection.additions} deletions={projection.deletions} unknownFiles={projection.unknownFiles} cumulative={projection.textChanges.some((change) => change.cumulative)} />
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
                <ChangeStats additions={change.additions} deletions={change.deletions} cumulative={change.cumulative === true} unknownFiles={change.missingPatch ? 1 : 0} />
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
      <p className="turn-result-card__note">行数来自已记录的补丁。准备中的补丁不代表已写入；重复编辑采用累计值。</p>
      {projection.textChanges.some((change) => change.state !== "committed") && (
        <p className="turn-result-card__note">本轮包含未完整提交或待核验的文件修改。</p>
      )}
    </article>
  );
}

export function TurnResults({ run, items, showTextChanges = true }: { run: Run; items: Item[]; showTextChanges?: boolean }) {
  const actions = useArtifacts();
  const [expanded, setExpanded] = useState(false);
  const projection = useMemo(() => projectTurnResults(items, run.id, actions?.executionRoot), [items, run.id, actions?.executionRoot]);
  const artifacts = projection.artifacts.filter((artifact) => artifact.kind !== "file" || !projection.textChanges.some((change) => change.path === artifact.path && change.additions !== undefined));
  const reply = items.filter((item) => item.runId === run.id && item.kind === "assistant_message" && item.status === "completed").sort((a, b) => b.ordinal - a.ordinal)[0];
  return (
    <section className="turn-results" aria-label="本轮结果">
      {reply?.content && <ReferencedArtifacts item={reply} excluded={projection.artifacts.map((artifact) => artifact.path)} />}
      {artifacts.slice(0, 3).map((artifact) => <ArtifactCard artifact={artifact} key={artifact.path} />)}
      {artifacts.length > 3 && <details onToggle={(event) => setExpanded(event.currentTarget.open)}><summary>另外 {artifacts.length - 3} 个结果文件</summary>{expanded && <ArtifactList artifacts={artifacts.slice(3)} />}</details>}
      {showTextChanges && projection.textChanges.length > 0 && <TextChangeCard projection={projection} />}
    </section>
  );
}

interface FileReferenceNode {
  type: string;
  url?: string;
  identifier?: string;
  value?: string;
  children?: FileReferenceNode[];
}

/** Reuse Markdown's parser for links; code samples and plain paths are not claims. */
function ReferencedArtifacts({ item, excluded }: { item: Item; excluded: string[] }) {
  const actions = useArtifacts();
  const [limit, setLimit] = useState(3);
  const [hasMore, setHasMore] = useState(false);
  const projection = useMemo(() => {
    let remaining = false;
    const plugin = () => (tree: FileReferenceNode) => {
      const definitions = new Map<string, string>();
      for (const node of tree.children ?? []) if (node.type === "definition" && node.identifier && node.url) definitions.set(node.identifier, node.url);
      const paths = new Set<string>();
      const walk = (node: FileReferenceNode): void => {
        const url = node.type === "link" || node.type === "image" ? node.url
          : node.type === "linkReference" || node.type === "imageReference" ? definitions.get(node.identifier ?? "") : undefined;
        const path = url && actions ? artifactPath(url, actions.executionRoot) : undefined;
        if (path && !excluded.includes(path)) paths.add(path);
        for (const child of node.children ?? []) walk(child);
      };
      walk(tree);
      remaining = paths.size > limit;
      tree.children = [...paths].slice(0, limit).map((path) => ({ type: "paragraph", children: [{ type: "link", url: path, children: [{ type: "text", value: path }] }] }));
    };
    return { plugin, remaining: () => remaining };
  }, [item.content, actions?.executionRoot, excluded.join("\n"), limit]);
  // The parser runs during child rendering. Read its bounded-list marker after commit.
  useEffect(() => { setHasMore(projection.remaining()); }, [projection]);
  return <>
    <Markdown skipHtml remarkPlugins={[projection.plugin]} urlTransform={(url) => url}
      components={{ p: ({ children }) => <>{children}</>, a: ({ href }) => href ? <ArtifactCard artifact={{ path: href, kind: artifactKind(href), runId: item.runId, itemId: item.id, state: "referenced" }} /> : null }}>
      {item.content ?? ""}
    </Markdown>
    {hasMore && <button type="button" onClick={() => setLimit((value) => value + 20)}>显示更多引用文件</button>}
  </>;
}

function ArtifactList({ artifacts, compact = false }: { artifacts: TurnArtifact[]; compact?: boolean }) {
  const [limit, setLimit] = useState(20);
  return <>{artifacts.slice(0, limit).map((artifact) => <ArtifactCard key={artifact.path} artifact={artifact} compact={compact} />)}
    {artifacts.length > limit && <button type="button" onClick={() => setLimit((value) => value + 20)}>再显示 20 个文件</button>}</>;
}

export function OutputContent({ artifacts, loading = false, error, hasMore = false, onLoadMore }: {
  artifacts: TurnArtifact[];
  hasMore?: boolean;
  onLoadMore?: (() => void) | undefined;
  loading?: boolean;
  error?: string | undefined;
}) {
  return (
    <section className="environment-output" aria-label="输出内容">
      <header className="environment-output__header"><h2>输出内容</h2><span>{artifacts.length || ""}</span></header>
      <p>文件来自已加载的工具记录。打开时显示当前版本。</p>
      {hasMore && <button type="button" disabled={loading} onClick={onLoadMore}>加载更早的结果记录</button>}
      {loading && <p className="environment-output__status" role="status">正在读取更早的输出…</p>}
      {error && <p className="environment-output__status" role="alert">更早的输出暂时无法读取：{error}</p>}
      {artifacts.length > 0
        ? <div className="environment-output__list"><ArtifactList artifacts={artifacts} compact /></div>
        : <p className="environment-output__empty">当前会话还没有可打开的输出产物。</p>}
    </section>
  );
}
