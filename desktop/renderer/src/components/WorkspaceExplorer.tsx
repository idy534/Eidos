import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from "react";
import { Tree, type NodeRendererProps } from "react-arborist";

import type {
  WorkspaceDirectoryEntry,
  WorkspaceDirectoryListing,
  WorkspaceFilePreview,
} from "../contracts.js";
import { MarkdownContent } from "./MarkdownContent.js";


interface WorkspaceTreeNode extends WorkspaceDirectoryEntry {
  id: string;
  children?: WorkspaceTreeNode[];
  loaded?: boolean;
}

interface WorkspaceExplorerProps {
  sessionId: string;
  executionKey?: string;
  layout?: "side" | "expanded";
  onSelectedFileChange?: (path?: string) => void;
  listDirectory?: (
    sessionId: string,
    path: string,
  ) => Promise<WorkspaceDirectoryListing>;
  readPreview?: (
    sessionId: string,
    path: string,
  ) => Promise<WorkspaceFilePreview>;
  subscribeChanges?: (
    sessionId: string,
    callback: (paths: string[]) => void,
  ) => () => void;
}

type ExplorerLayout = "side" | "expanded";

const SIDE_SPLIT_MIN = 11 * 16;
const EXPANDED_SPLIT_MIN = 13 * 16;
const EXPANDED_SPLIT_MAX = 24 * 16;

const defaultListDirectory = (sessionId: string, path: string) =>
  window.eidosRuntime.listWorkspaceDirectory(sessionId, path);
const defaultReadPreview = (sessionId: string, path: string) =>
  window.eidosRuntime.readWorkspaceFilePreview(sessionId, path);
const defaultSubscribeChanges = (
  sessionId: string,
  callback: (paths: string[]) => void,
) => window.eidosRuntime?.onNotification((notification) => {
  if (
    notification.method === "workspace/changed"
    && notification.params.sessionId === sessionId
  ) {
    callback(notification.params.paths);
  }
}) ?? (() => undefined);

function toNodes(
  entries: WorkspaceDirectoryEntry[],
  previous: WorkspaceTreeNode[] = [],
): WorkspaceTreeNode[] {
  const byId = new Map(previous.map((node) => [node.id, node]));
  return entries.filter((entry) => entry.name !== ".git").map((entry) => {
    const existing = byId.get(entry.relativePath);
    return {
      ...entry,
      id: entry.relativePath,
      ...(entry.kind === "directory"
        ? {
            children: existing?.children ?? [],
            loaded: existing?.loaded ?? false,
          }
        : {}),
    };
  });
}

function replaceDirectoryChildren(
  nodes: WorkspaceTreeNode[],
  id: string,
  entries: WorkspaceDirectoryEntry[],
): WorkspaceTreeNode[] {
  return nodes.map((node) => {
    if (node.id === id) {
      return { ...node, children: toNodes(entries, node.children), loaded: true };
    }
    if (!node.children?.length) return node;
    return {
      ...node,
      children: replaceDirectoryChildren(node.children, id, entries),
    };
  });
}

function findNode(nodes: WorkspaceTreeNode[], id: string): WorkspaceTreeNode | undefined {
  for (const node of nodes) {
    if (node.id === id) return node;
    const nested = node.children ? findNode(node.children, id) : undefined;
    if (nested) return nested;
  }
  return undefined;
}

export function WorkspaceExplorer({
  sessionId,
  executionKey = sessionId,
  layout = "expanded",
  onSelectedFileChange,
  listDirectory = defaultListDirectory,
  readPreview = defaultReadPreview,
  subscribeChanges = defaultSubscribeChanges,
}: WorkspaceExplorerProps) {
  const [nodes, setNodes] = useState<WorkspaceTreeNode[]>([]);
  const [rootTruncated, setRootTruncated] = useState(false);
  const [loadingRoot, setLoadingRoot] = useState(true);
  const [loadingPath, setLoadingPath] = useState<string>();
  const [previews, setPreviews] = useState<Record<string, WorkspaceFilePreview>>({});
  const [openPreviewPaths, setOpenPreviewPaths] = useState<string[]>([]);
  const [activePreviewPath, setActivePreviewPath] = useState<string>();
  const [previewLoadingPath, setPreviewLoadingPath] = useState<string>();
  const [error, setError] = useState<string>();
  const [treeHeight, setTreeHeight] = useState(320);
  const [splitSizes, setSplitSizes] = useState<Partial<Record<ExplorerLayout, number>>>({});
  const requestVersion = useRef(0);
  const explorerRef = useRef<HTMLElement>(null);
  const treeContainerRef = useRef<HTMLDivElement>(null);
  const previewsRef = useRef(previews);
  const selectedFileCallbackRef = useRef(onSelectedFileChange);
  const nodesRef = useRef(nodes);
  const openPreviewPathsRef = useRef(openPreviewPaths);
  const loadDirectoryRef = useRef<(path: string, force?: boolean) => void>(() => undefined);
  const openFileRef = useRef<(path: string, refresh?: boolean) => void>(() => undefined);
  const splitterDragRef = useRef<{
    pointerId: number;
    layout: ExplorerLayout;
    startCoordinate: number;
    startSize: number;
  } | undefined>(undefined);
  previewsRef.current = previews;
  selectedFileCallbackRef.current = onSelectedFileChange;
  nodesRef.current = nodes;
  openPreviewPathsRef.current = openPreviewPaths;

  useEffect(() => {
    const version = ++requestVersion.current;
    setNodes([]);
    setPreviews({});
    setOpenPreviewPaths([]);
    setActivePreviewPath(undefined);
    setPreviewLoadingPath(undefined);
    setError(undefined);
    setLoadingRoot(true);
    void listDirectory(sessionId, ".")
      .then((listing) => {
        if (requestVersion.current !== version) return;
        setNodes(toNodes(listing.entries));
        setRootTruncated(listing.truncated);
      })
      .catch((cause: unknown) => {
        if (requestVersion.current === version) {
          setError(cause instanceof Error ? cause.message : "Workspace 目录读取失败");
        }
      })
      .finally(() => {
        if (requestVersion.current === version) setLoadingRoot(false);
      });
  }, [executionKey, sessionId, listDirectory]);

  useEffect(() => {
    selectedFileCallbackRef.current?.(activePreviewPath);
  }, [activePreviewPath, executionKey, sessionId]);

  useEffect(() => {
    const container = treeContainerRef.current;
    if (!container || typeof ResizeObserver === "undefined") return;
    const resize = () => {
      const height = Math.floor(container.getBoundingClientRect().height);
      if (height > 0) setTreeHeight(height);
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(container);
    return () => observer.disconnect();
  }, [layout]);

  const loadDirectory = useCallback((path: string, force = false) => {
    const node = findNode(nodes, path);
    if (
      path !== "."
      && (!node || node.kind !== "directory" || (node.loaded && !force))
    ) return;
    if (loadingPath === path) return;
    setLoadingPath(path);
    setError(undefined);
    const version = requestVersion.current;
    void listDirectory(sessionId, path)
      .then((listing) => {
        if (requestVersion.current !== version) return;
        if (path === ".") {
          setNodes((current) => toNodes(listing.entries, current));
          setRootTruncated(listing.truncated);
        } else {
          setNodes((current) => replaceDirectoryChildren(current, path, listing.entries));
        }
        if (listing.truncated) setError(`${path} 的目录内容已截断`);
      })
      .catch((cause: unknown) => {
        if (requestVersion.current !== version) return;
        setError(cause instanceof Error ? cause.message : "Workspace 目录读取失败");
      })
      .finally(() => {
        if (requestVersion.current === version) {
          setLoadingPath((current) => current === path ? undefined : current);
        }
      });
  }, [listDirectory, loadingPath, nodes, sessionId]);

  const openFile = useCallback((path: string, refresh = false) => {
    setOpenPreviewPaths((current) => current.includes(path) ? current : [...current, path]);
    setActivePreviewPath(path);
    if (!refresh && previewsRef.current[path]) return;
    setPreviewLoadingPath(path);
    setError(undefined);
    selectedFileCallbackRef.current?.(path);
    const version = requestVersion.current;
    void readPreview(sessionId, path)
      .then((nextPreview) => {
        if (requestVersion.current === version) {
          setPreviews((current) => ({ ...current, [path]: nextPreview }));
        }
      })
      .catch((cause: unknown) => {
        if (requestVersion.current !== version) return;
        setError(cause instanceof Error ? cause.message : "文件预览读取失败");
      })
      .finally(() => {
        if (requestVersion.current === version) {
          setPreviewLoadingPath((current) => current === path ? undefined : current);
        }
      });
  }, [readPreview, sessionId]);

  loadDirectoryRef.current = loadDirectory;
  openFileRef.current = openFile;

  const closePreview = useCallback((path: string) => {
    const index = openPreviewPaths.indexOf(path);
    const next = openPreviewPaths.filter((item) => item !== path);
    setOpenPreviewPaths(next);
    if (activePreviewPath === path) {
      setActivePreviewPath(next[Math.min(index, next.length - 1)]);
    }
  }, [activePreviewPath, openPreviewPaths]);

  function getSplitBounds(targetLayout: ExplorerLayout = layout): { min: number; max: number } {
    const rect = explorerRef.current?.getBoundingClientRect();
    const width = rect?.width || Math.max(640, window.innerWidth);
    const height = rect?.height || 600;
    if (targetLayout === "side") {
      const min = Math.min(SIDE_SPLIT_MIN, Math.max(176, height - SIDE_SPLIT_MIN));
      return { min, max: Math.max(min, height - SIDE_SPLIT_MIN) };
    }
    const min = Math.min(EXPANDED_SPLIT_MIN, Math.max(208, width - 20 * 16));
    return { min, max: Math.max(min, Math.min(EXPANDED_SPLIT_MAX, width - 20 * 16)) };
  }

  function getSplitSize(targetLayout: ExplorerLayout = layout): number {
    const current = splitSizes[targetLayout];
    if (current !== undefined) return current;
    const rect = explorerRef.current?.getBoundingClientRect();
    if (targetLayout === "side") return Math.round((rect?.height || 600) * 0.42);
    return 18 * 16;
  }

  function setSplitSize(size: number, targetLayout: ExplorerLayout = layout): void {
    const { min, max } = getSplitBounds(targetLayout);
    setSplitSizes((current) => ({
      ...current,
      [targetLayout]: Math.min(max, Math.max(min, Math.round(size))),
    }));
  }

  function handleSplitterPointerDown(event: ReactPointerEvent<HTMLDivElement>): void {
    splitterDragRef.current = {
      pointerId: event.pointerId,
      layout,
      startCoordinate: layout === "side" ? event.clientY : event.clientX,
      startSize: getSplitSize(layout),
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
    event.preventDefault();
  }

  function handleSplitterPointerMove(event: ReactPointerEvent<HTMLDivElement>): void {
    const drag = splitterDragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const delta = drag.layout === "side"
      ? event.clientY - drag.startCoordinate
      : drag.startCoordinate - event.clientX;
    setSplitSize(drag.startSize + delta, drag.layout);
  }

  function finishSplitterResize(event: ReactPointerEvent<HTMLDivElement>): void {
    if (splitterDragRef.current?.pointerId === event.pointerId) splitterDragRef.current = undefined;
  }

  function handleSplitterKeyDown(event: ReactKeyboardEvent<HTMLDivElement>): void {
    const increase = layout === "side" ? event.key === "ArrowDown" : event.key === "ArrowLeft";
    const decrease = layout === "side" ? event.key === "ArrowUp" : event.key === "ArrowRight";
    if (increase) {
      event.preventDefault();
      setSplitSize(getSplitSize() + 16);
    } else if (decrease) {
      event.preventDefault();
      setSplitSize(getSplitSize() - 16);
    } else if (event.key === "Home") {
      event.preventDefault();
      setSplitSize(getSplitBounds().min);
    } else if (event.key === "End") {
      event.preventDefault();
      setSplitSize(getSplitBounds().max);
    }
  }

  useEffect(() => subscribeChanges(sessionId, (paths) => {
    const parents = new Set(paths.map((path) => {
      const slash = path.lastIndexOf("/");
      return slash < 0 ? "." : path.slice(0, slash);
    }));
    for (const parent of parents) {
      if (parent === "." || findNode(nodesRef.current, parent)?.loaded) {
        loadDirectoryRef.current(parent, true);
      }
    }
    for (const path of openPreviewPathsRef.current) {
      if (paths.includes(path)) openFileRef.current(path, true);
    }
  }), [executionKey, sessionId, subscribeChanges]);

  const renderNode = useCallback((props: NodeRendererProps<WorkspaceTreeNode>) => (
    <WorkspaceTreeRow
      {...props}
      loading={loadingPath === props.node.id}
      onOpenDirectory={loadDirectory}
      onOpenFile={openFile}
    />
  ), [loadDirectory, loadingPath, openFile]);

  const splitSize = splitSizes[layout];
  const splitBounds = getSplitBounds(layout);
  const explorerStyle = splitSize === undefined ? undefined : {
    "--workspace-tree-size": `${splitSize}px`,
    "--workspace-tree-divider": `${splitSize}px`,
  } as CSSProperties;

  return (
    <section
      ref={explorerRef}
      className={`workspace-explorer workspace-explorer--${layout}`}
      style={explorerStyle}
      aria-label="Workspace 文件浏览器"
    >
      <aside className="workspace-tree-pane" aria-label="文件树">
        <div className="workspace-explorer-heading">
          <strong>Files</strong>
          {loadingRoot && <span role="status">正在读取…</span>}
        </div>
        {error && <p className="workspace-explorer-error" role="alert">{error}</p>}
        {rootTruncated && <p className="workspace-tree-note">目录内容已截断</p>}
        {!loadingRoot && nodes.length === 0 ? (
          <p className="workspace-empty-state">Workspace 中没有可显示的文件</p>
        ) : (
          <div className="workspace-tree-scroll" ref={treeContainerRef}>
            <Tree<WorkspaceTreeNode>
              data={nodes}
              width="100%"
              height={treeHeight}
              rowHeight={28}
              indent={16}
              overscanCount={8}
              openByDefault={false}
              disableDrag
              disableDrop
              disableEdit
              disableMultiSelection
              selectionFollowsFocus
              aria-label="Workspace 文件树"
            >
              {renderNode}
            </Tree>
          </div>
        )}
      </aside>
      <div
        className={`workspace-explorer__splitter workspace-explorer__splitter--${layout}`}
        role="separator"
        aria-label="调整文件树大小"
        aria-orientation={layout === "side" ? "horizontal" : "vertical"}
        aria-valuemin={splitBounds.min}
        aria-valuemax={splitBounds.max}
        aria-valuenow={splitSize ?? getSplitSize(layout)}
        tabIndex={0}
        onPointerDown={handleSplitterPointerDown}
        onPointerMove={handleSplitterPointerMove}
        onPointerUp={finishSplitterResize}
        onPointerCancel={finishSplitterResize}
        onKeyDown={handleSplitterKeyDown}
      />
      <div className="workspace-preview-pane" aria-live="polite">
        {openPreviewPaths.length > 0 && (
          <div className="workspace-preview-tabs" role="tablist" aria-label="打开的文件">
            {openPreviewPaths.map((path) => (
              <div className="workspace-preview-tab" key={path}>
                <button
                  type="button"
                  role="tab"
                  aria-selected={activePreviewPath === path}
                  onClick={() => setActivePreviewPath(path)}
                >
                  {path.split("/").pop() ?? path}
                </button>
                <button
                  type="button"
                  className="workspace-preview-tab-close"
                  aria-label={`关闭 ${path.split("/").pop() ?? path}`}
                  onClick={() => closePreview(path)}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}
        {previewLoadingPath === activePreviewPath && !previews[activePreviewPath ?? ""] ? (
          <p className="workspace-preview-placeholder" role="status">正在读取文件…</p>
        ) : activePreviewPath && previews[activePreviewPath] ? (
          <WorkspacePreview preview={previews[activePreviewPath]} />
        ) : (
          <p className="workspace-preview-placeholder">选择文件以查看预览</p>
        )}
      </div>
    </section>
  );
}

function WorkspaceTreeRow({
  node,
  style,
  dragHandle,
  loading,
  onOpenDirectory,
  onOpenFile,
}: NodeRendererProps<WorkspaceTreeNode> & {
  loading: boolean;
  onOpenDirectory(path: string): void;
  onOpenFile(path: string): void;
}) {
  return (
    <div
      ref={dragHandle}
      style={style}
      className={`workspace-tree-row${node.isSelected ? " is-selected" : ""}`}
      onClick={() => {
        node.select();
        if (node.data.kind === "file") onOpenFile(node.id);
      }}
      onDoubleClick={() => {
        if (node.data.kind === "directory") {
          onOpenDirectory(node.id);
          node.toggle();
        }
      }}
    >
      <button
        type="button"
        className="workspace-tree-chevron"
        aria-label={node.isOpen ? `收起 ${node.data.name}` : `展开 ${node.data.name}`}
        disabled={node.data.kind !== "directory"}
        onClick={(event) => {
          event.stopPropagation();
          if (node.data.kind === "directory") {
            onOpenDirectory(node.id);
            node.toggle();
          }
        }}
      >
        {node.data.kind === "directory" ? (node.isOpen ? "⌄" : "›") : ""}
      </button>
      <span className="workspace-tree-icon" aria-hidden="true">
        {node.data.kind === "directory" ? "▰" : "▤"}
      </span>
      <span className="workspace-tree-name">{node.data.name}</span>
      {loading && <span className="workspace-tree-loading" aria-label="正在读取">…</span>}
    </div>
  );
}

function WorkspacePreview({ preview }: { preview: WorkspaceFilePreview }) {
  return (
    <article className="workspace-preview">
      <header className="workspace-preview-header">
        <span title={preview.path}>{preview.path}</span>
        <span>{formatBytes(preview.sizeBytes)}</span>
      </header>
      {preview.truncated && <p className="workspace-preview-notice">预览已截断</p>}
      {preview.kind === "unavailable" ? (
        <div className="workspace-preview-unavailable">
          <strong>{preview.reason === "binary" ? "二进制文件无法预览" : "此文件类型暂不支持预览"}</strong>
          <span>请在外部编辑器中打开该文件。</span>
        </div>
      ) : preview.kind === "markdown" ? (
        <div className="workspace-markdown-preview">
          <MarkdownContent content={preview.content ?? ""} />
        </div>
      ) : preview.kind === "code" ? (
        <ShikiPreview
          code={preview.content ?? ""}
          {...(preview.language === undefined ? {} : { language: preview.language })}
        />
      ) : (
        <pre className="workspace-text-preview"><code>{preview.content}</code></pre>
      )}
    </article>
  );
}

function ShikiPreview({ code, language }: { code: string; language?: string }) {
  const [html, setHtml] = useState<string>();
  useEffect(() => {
    let active = true;
    setHtml(undefined);
    void import("shiki/bundle/web").then(async ({ codeToHtml }) => {
      const rendered = await codeToHtml(code, {
        lang: language ?? "text",
        theme: "github-light-default",
      });
      if (active) setHtml(rendered);
    });
    return () => { active = false; };
  }, [code, language]);
  if (!html) return <pre className="workspace-text-preview"><code>{code}</code></pre>;
  return <div className="workspace-code-preview" dangerouslySetInnerHTML={{ __html: html }} />;
}

function formatBytes(value: number): string {
  if (value < 1_024) return `${value} B`;
  if (value < 1_024 * 1_024) return `${(value / 1_024).toFixed(1)} KB`;
  return `${(value / (1_024 * 1_024)).toFixed(1)} MB`;
}
