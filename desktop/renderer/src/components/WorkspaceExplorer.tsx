import { FileFind } from "./FileFind.js";
import { useInputContext } from "./InputContext.js";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from "react";
import { Tree, type NodeRendererProps } from "react-arborist";

import type {
  WorkspaceDirectoryEntry,
  WorkspaceDirectoryListing,
  WorkspaceFilePreview,
} from "../contracts.js";
import { runtimeBusinessCode, userFacingError } from "../session-state.js";
import { useArtifacts, usePreviewUrl, type WorkspaceFilePosition } from "./ArtifactContext.js";
import { ContextMenu } from "./DropdownMenu.js";
import { ToastContainer } from "./ToastContainer.js";
import { FinderIcon, CopyIcon, ExternalLinkIcon, CloseIcon, CloseOthersIcon } from "./MenuIcons.js";
import { WorkspaceFileIcon } from "./WorkspaceFileIcon.js";
import { MarkdownContent } from "./MarkdownContent.js";
import { PdfViewer } from "./PdfViewer.js";
import { Tooltip } from "./Tooltip.js";


interface WorkspaceTreeNode extends WorkspaceDirectoryEntry {
  id: string;
  children?: WorkspaceTreeNode[];
  loaded?: boolean;
}

export interface WorkspaceFileOpenRequest extends WorkspaceFilePosition {
  path: string;
  requestId: number;
}

interface WorkspaceExplorerProps {
  sessionId: string;
  executionKey?: string;
  layout?: "side" | "expanded";
  openRequest?: WorkspaceFileOpenRequest | undefined;
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

const SIDE_SPLIT_MIN = 5.5 * 16;
const EXPANDED_SPLIT_MIN = 13 * 16;
const EXPANDED_SPLIT_DEFAULT = 18 * 16;
const INVALID_FILE_PATH_ERROR = "文件路径无效。请从当前文件树重新选择。";

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

function isValidProgrammaticFilePath(value: string): boolean {
  if (
    !value
    || value === "."
    || value.length > 4096
    || value.startsWith("/")
    || /[\u0000-\u001f\u007f]/.test(value)
  ) return false;
  return !value.split("/").some((part) => part === "" || part === "." || part === "..");
}


function previewReadError(cause: unknown): string {
  return runtimeBusinessCode(cause)
    ? userFacingError(cause)
    : "文件预览读取失败";
}

export function WorkspaceExplorer({
  sessionId,
  executionKey = sessionId,
  layout = "expanded",
  openRequest,
  onSelectedFileChange,
  listDirectory = defaultListDirectory,
  readPreview = defaultReadPreview,
  subscribeChanges = defaultSubscribeChanges,
}: WorkspaceExplorerProps) {
  const actions = useArtifacts();
  const [treeContextMenu, setTreeContextMenu] = useState<{
    path: string;
    name: string;
    kind: "file" | "directory";
    x: number;
    y: number;
    element: HTMLElement;
  } | undefined>(undefined);
  const [previewTabMenu, setPreviewTabMenu] = useState<{
    path: string;
    x: number;
    y: number;
    element: HTMLElement;
  } | undefined>(undefined);
  const [nodes, setNodes] = useState<WorkspaceTreeNode[]>([]);
  const [rootTruncated, setRootTruncated] = useState(false);
  const [loadingRoot, setLoadingRoot] = useState(true);
  const [loadingPath, setLoadingPath] = useState<string>();
  const [previews, setPreviews] = useState<Record<string, WorkspaceFilePreview>>({});
  const [openPreviewPaths, setOpenPreviewPaths] = useState<string[]>([]);
  const [activePreviewPath, setActivePreviewPath] = useState<string>();
  const [previewNavigation, setPreviewNavigation] = useState<WorkspaceFileOpenRequest>();
  const [previewLoadingPath, setPreviewLoadingPath] = useState<string>();
  const [error, setError] = useState<string>();
  const [treeHeight, setTreeHeight] = useState(320);
  const [splitSizes, setSplitSizes] = useState<Partial<Record<ExplorerLayout, number>>>({});
  const requestVersion = useRef(0);
  const explorerRef = useRef<HTMLElement>(null);
  const sideTreeWidthRef = useRef<number | undefined>(undefined);
  const expandedSplitAdjustedRef = useRef(false);
  const treeResizeObserverRef = useRef<ResizeObserver | null>(null);
  const previewsRef = useRef(previews);
  const selectedFileCallbackRef = useRef(onSelectedFileChange);
  const nodesRef = useRef(nodes);
  const openPreviewPathsRef = useRef(openPreviewPaths);
  const loadDirectoryRef = useRef<(path: string, force?: boolean) => void>(() => undefined);
  const openFileRef = useRef<(path: string, refresh?: boolean) => void>(() => undefined);
  const handledRequestIdRef = useRef<number | undefined>(undefined);
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

  useLayoutEffect(() => {
    const explorer = explorerRef.current;
    if (!explorer) return;
    if (layout === "expanded") {
      if (expandedSplitAdjustedRef.current) return;
      const { min, max } = getSplitBounds("expanded");
      const size = Math.min(max, Math.max(min, sideTreeWidthRef.current ?? EXPANDED_SPLIT_DEFAULT));
      setSplitSizes((current) => current.expanded === size ? current : { ...current, expanded: size });
      return;
    }
    const measure = () => {
      // Keep the last side width through the Dock's expansion transition.
      if (!explorer.classList.contains("workspace-explorer--side")) return;
      const width = explorer.getBoundingClientRect().width;
      if (width > 0) sideTreeWidthRef.current = Math.round(width);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(explorer);
    return () => observer.disconnect();
  }, [layout]);

  useEffect(() => {
    const version = ++requestVersion.current;
    setNodes([]);
    setPreviews({});
    setOpenPreviewPaths([]);
    setActivePreviewPath(undefined);
    setPreviewNavigation(undefined);
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

  const setTreeContainerRef = useCallback((container: HTMLDivElement | null) => {
    treeResizeObserverRef.current?.disconnect();
    treeResizeObserverRef.current = null;
    if (!container || typeof ResizeObserver === "undefined") return;
    const resize = () => {
      const height = container.clientHeight || Math.floor(container.getBoundingClientRect().height);
      if (height > 0) setTreeHeight(height);
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(container);
    treeResizeObserverRef.current = observer;
  }, []);

  useEffect(() => () => {
    treeResizeObserverRef.current?.disconnect();
    treeResizeObserverRef.current = null;
  }, []);

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

  const previewSequences = useRef(new Map<string, number>());
  const openFile = useCallback((path: string, refresh = false, navigation?: WorkspaceFileOpenRequest) => {
    if (!refresh) setPreviewNavigation(navigation);
    setOpenPreviewPaths((current) => current.includes(path) ? current : [...current, path]);
    setActivePreviewPath(path);
    if (!refresh && previewsRef.current[path]) return;
    if (refresh) setPreviews((current) => { const next = { ...current }; delete next[path]; return next; });
    setPreviewLoadingPath(path);
    setError(undefined);
    selectedFileCallbackRef.current?.(path);
    const version = requestVersion.current;
    const sequence = (previewSequences.current.get(path) ?? 0) + 1;
    previewSequences.current.set(path, sequence);
    void readPreview(sessionId, path)
      .then((nextPreview) => {
        if (requestVersion.current === version && previewSequences.current.get(path) === sequence) {
          setPreviews((current) => ({ ...current, [path]: nextPreview }));
        }
      })
      .catch((cause: unknown) => {
        if (requestVersion.current !== version || previewSequences.current.get(path) !== sequence) return;
        setPreviews((current) => { const next = { ...current }; delete next[path]; return next; });
        setError(previewReadError(cause));
      })
      .finally(() => {
        if (requestVersion.current === version && previewSequences.current.get(path) === sequence) {
          setPreviewLoadingPath((current) => current === path ? undefined : current);
        }
      });
  }, [readPreview, sessionId]);

  loadDirectoryRef.current = loadDirectory;
  openFileRef.current = openFile;

  const openRequestedFile = useCallback((request: WorkspaceFileOpenRequest) => {
    if (!isValidProgrammaticFilePath(request.path)) {
      setError(INVALID_FILE_PATH_ERROR);
      return;
    }
    openFile(request.path, false, request);
  }, [openFile]);

  // Respond to programmatic file open requests from parent
  useEffect(() => {
    if (!openRequest) return;
    if (handledRequestIdRef.current === openRequest.requestId) return;
    handledRequestIdRef.current = openRequest.requestId;
    void openRequestedFile(openRequest);
  }, [openRequest, openRequestedFile]);

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
    // The Dock animates its width; expanded bounds use the final workspace space.
    const workspaceWidth = targetLayout === "expanded" && layout === "expanded"
      ? explorerRef.current?.closest(".workspace-body")?.getBoundingClientRect().width
      : undefined;
    const width = workspaceWidth || rect?.width || Math.max(640, window.innerWidth);
    const height = rect?.height || 600;
    if (targetLayout === "side") {
      return {
        min: SIDE_SPLIT_MIN,
        max: Math.max(SIDE_SPLIT_MIN, height - SIDE_SPLIT_MIN),
      };
    }
    const min = Math.min(EXPANDED_SPLIT_MIN, Math.max(208, width - 20 * 16));
    return { min, max: Math.max(min, width - 20 * 16) };
  }

  function getSplitSize(targetLayout: ExplorerLayout = layout): number {
    const current = splitSizes[targetLayout];
    if (current !== undefined) return current;
    if (targetLayout === "side") return SIDE_SPLIT_MIN;
    return EXPANDED_SPLIT_DEFAULT;
  }

  function setSplitSize(size: number, targetLayout: ExplorerLayout = layout): void {
    const { min, max } = getSplitBounds(targetLayout);
    if (targetLayout === "expanded") expandedSplitAdjustedRef.current = true;
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
      : event.clientX - drag.startCoordinate;
    setSplitSize(drag.startSize + delta, drag.layout);
  }

  function finishSplitterResize(event: ReactPointerEvent<HTMLDivElement>): void {
    if (splitterDragRef.current?.pointerId === event.pointerId) splitterDragRef.current = undefined;
  }

  function handleSplitterKeyDown(event: ReactKeyboardEvent<HTMLDivElement>): void {
    const increase = layout === "side" ? event.key === "ArrowDown" : event.key === "ArrowRight";
    const decrease = layout === "side" ? event.key === "ArrowUp" : event.key === "ArrowLeft";
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
      if (paths.some((changed) => changed === path || path.startsWith(changed + "/"))) openFileRef.current(path, true);
    }
  }), [executionKey, sessionId, subscribeChanges]);

  const handleTreeContextMenu = useCallback((
    entry: WorkspaceDirectoryEntry,
    event: React.MouseEvent<HTMLDivElement>,
  ) => {
    event.preventDefault();
    setTreeContextMenu({
      path: entry.relativePath,
      name: entry.name,
      kind: entry.kind,
      x: event.clientX,
      y: event.clientY,
      element: event.currentTarget,
    });
  }, []);

  const renderNode = useCallback((props: NodeRendererProps<WorkspaceTreeNode>) => (
    <WorkspaceTreeRow
      {...props}
      loading={loadingPath === props.node.id}
      onOpenDirectory={loadDirectory}
      onOpenFile={openFile}
      onContextMenu={handleTreeContextMenu}
    />
  ), [handleTreeContextMenu, loadDirectory, loadingPath, openFile]);

  const splitSize = splitSizes[layout];
  const splitBounds = getSplitBounds(layout);
  const explorerStyle = splitSize === undefined ? undefined : {
    "--workspace-tree-size": `${splitSize}px`,
    "--workspace-tree-divider": `${splitSize}px`,
  } as CSSProperties;
  const activePreview = activePreviewPath ? previews[activePreviewPath] : undefined;

  return (
    <section
      ref={explorerRef}
      className={`workspace-explorer workspace-explorer--${layout}`}
      style={explorerStyle}
      aria-label="Workspace 文件浏览器"
    >
      <aside className="workspace-tree-pane" aria-label="文件树">
        {error && (
          <ToastContainer
            toasts={[{ id: "workspace-explorer-error", message: error, type: "error", duration: 3500 }]}
            onDismiss={() => setError(undefined)}
          />
        )}
        {rootTruncated && <p className="workspace-tree-note">目录内容已截断</p>}
        {loadingRoot ? (
          <p className="workspace-empty-state" role="status">正在读取…</p>
        ) : nodes.length === 0 ? (
          <p className="workspace-empty-state">Workspace 中没有可显示的文件</p>
        ) : (
          <div className="workspace-tree-scroll" ref={setTreeContainerRef}>
            <Tree<WorkspaceTreeNode>
              data={nodes}
              width="100%"
              height={treeHeight}
              className="workspace-tree-list"
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
          <div className="workspace-preview-bar">
            <div className="workspace-preview-tabs" role="tablist" aria-label="打开的文件">
              {openPreviewPaths.map((path) => {
                const fileName = path.split("/").pop() ?? path;
                return (
                  <div className="workspace-preview-tab" key={path}>
                    <button
                      type="button"
                      role="tab"
                      aria-label={path}
                      aria-selected={activePreviewPath === path}
                      title={path}
                      onClick={() => setActivePreviewPath(path)}
                      onContextMenu={(event) => {
                        event.preventDefault();
                        setPreviewTabMenu({
                          path,
                          x: event.clientX,
                          y: event.clientY,
                          element: event.currentTarget,
                        });
                      }}
                    >
                      <WorkspaceFileIcon
                        name={fileName}
                        size={14}
                        className="workspace-preview-tab-icon file-icon-svg"
                      />
                      <span className="workspace-preview-tab-name">{fileName}</span>
                    </button>
                    <button
                      type="button"
                      className="workspace-preview-tab-close"
                      aria-label={`关闭 ${fileName}`}
                      onClick={() => closePreview(path)}
                    >
                      ×
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        )}
        {previewLoadingPath !== undefined && previewLoadingPath === activePreviewPath && !activePreview ? (
          <p className="workspace-preview-placeholder" role="status">正在读取文件…</p>
        ) : activePreview ? (
          <WorkspacePreview key={`${executionKey}:${activePreview.path}:${activePreview.version ?? ""}`} preview={activePreview}
            navigation={previewNavigation?.path === activePreview.path ? previewNavigation : undefined} />
        ) : (
          <div className="workspace-preview-placeholder">{activePreviewPath ? "文件预览不可用" : "选择文件以查看预览"}</div>
        )}
      </div>

      {treeContextMenu && (
        <ContextMenu
          x={treeContextMenu.x}
          y={treeContextMenu.y}
          label={`文件操作：${treeContextMenu.name}`}
          restoreFocusElement={treeContextMenu.element}
          onClose={() => setTreeContextMenu(undefined)}
          items={[
            ...(actions?.showInFinder
              ? [
                  {
                    key: "finder",
                    label: "在 Finder 中显示",
                    icon: <FinderIcon />,
                    onClick: () => {
                      setTreeContextMenu(undefined);
                      void actions.showInFinder?.(treeContextMenu.path);
                    },
                  },
                ]
              : []),
            {
              key: "copy-relative",
              label: "复制相对路径",
              icon: <CopyIcon />,
              onClick: () => {
                setTreeContextMenu(undefined);
                void navigator?.clipboard?.writeText(treeContextMenu.path);
              },
            },
            {
              key: "copy-absolute",
              label: "复制绝对路径",
              icon: <CopyIcon />,
              onClick: () => {
                setTreeContextMenu(undefined);
                const fullPath = actions?.executionRoot
                  ? `${actions.executionRoot.replace(/\/$/, "")}/${treeContextMenu.path}`
                  : treeContextMenu.path;
                void navigator?.clipboard?.writeText(fullPath);
              },
            },
            ...(treeContextMenu.kind === "file" && actions?.openExternal
              ? [
                  {
                    key: "external",
                    label: "系统应用打开",
                    icon: <ExternalLinkIcon />,
                    onClick: () => {
                      setTreeContextMenu(undefined);
                      void actions.openExternal?.(treeContextMenu.path);
                    },
                  },
                ]
              : []),
          ]}
        />
      )}

      {previewTabMenu && (
        <ContextMenu
          x={previewTabMenu.x}
          y={previewTabMenu.y}
          label={`标签操作：${previewTabMenu.path.split("/").pop() ?? previewTabMenu.path}`}
          restoreFocusElement={previewTabMenu.element}
          onClose={() => setPreviewTabMenu(undefined)}
          items={[
            {
              key: "close",
              label: "关闭标签页",
              icon: <CloseIcon />,
              onClick: () => {
                setPreviewTabMenu(undefined);
                closePreview(previewTabMenu.path);
              },
            },
            ...(openPreviewPaths.length > 1
              ? [
                  {
                    key: "close-others",
                    label: "关闭其他标签页",
                    icon: <CloseOthersIcon />,
                    onClick: () => {
                      const keep = previewTabMenu.path;
                      setPreviewTabMenu(undefined);
                      setOpenPreviewPaths([keep]);
                      setActivePreviewPath(keep);
                    },
                  },
                ]
              : []),
          ]}
        />
      )}
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
  onContextMenu,
}: NodeRendererProps<WorkspaceTreeNode> & {
  loading: boolean;
  onOpenDirectory(path: string): void;
  onOpenFile(path: string): void;
  onContextMenu?(entry: WorkspaceDirectoryEntry, event: React.MouseEvent<HTMLDivElement>): void;
}) {
  const inputContext = useInputContext();
  return (
    <div
      ref={dragHandle}
      style={style}
      className={`workspace-tree-row${node.isSelected ? " is-selected" : ""}`}
      onClick={() => {
        node.select();
        if (node.data.kind === "file") onOpenFile(node.id);
      }}
      onContextMenu={(event) => {
        onContextMenu?.(node.data, event);
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
        data-open={node.isOpen}
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
        {node.data.kind === "directory" && (
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="m6 3.5 4.5 4.5L6 12.5" />
          </svg>
        )}
      </button>
      <span className="workspace-tree-icon" aria-hidden="true">
        {node.data.kind === "directory" ? (
          <svg viewBox="0 0 20 20" className="workspace-tree-folder-icon">
            <path d="M2.5 5h5l1.5 2h8.5v9.5h-15zM2.5 7h15" />
          </svg>
        ) : (
          <WorkspaceFileIcon name={node.data.name} />
        )}
      </span>
      <span className="workspace-tree-name">{node.data.name}</span>
      {loading && <span className="workspace-tree-loading" aria-label="正在读取">…</span>}
      {inputContext && (
        <Tooltip content="添加到当前输入">
          <button
            type="button"
            className="workspace-reference-button"
            aria-label={`引用 ${node.data.name}`}
            title="添加到当前输入"
            onClick={(event) => {
              event.stopPropagation();
              void inputContext.add({
                kind: node.data.kind === "directory" ? "directory" : "file",
                source: `${inputContext.workspaceRoot}/${node.id}`,
              });
            }}
          >
            ＋
          </button>
        </Tooltip>
      )}
    </div>
  );
}

export { WorkspaceFileIcon } from "./WorkspaceFileIcon.js";

function selectPreviewPosition(container: HTMLElement, position: WorkspaceFilePosition, markdown: boolean): void {
  const line = position.line;
  if (line === undefined) return;
  const elements = Array.from(container.querySelectorAll<HTMLElement>("[data-source-line]"));
  const target = markdown
    ? elements.filter((element) => Number(element.dataset.sourceLine) <= line && Number(element.dataset.sourceEndLine) >= line)
      .sort((left, right) => (Number(left.dataset.sourceEndLine) - Number(left.dataset.sourceLine)) - (Number(right.dataset.sourceEndLine) - Number(right.dataset.sourceLine)))[0]
      ?? elements.sort((left, right) => Math.abs(Number(left.dataset.sourceLine) - line) - Math.abs(Number(right.dataset.sourceLine) - line))[0]
    : elements.find((element) => Number(element.dataset.sourceLine) === line);
  if (!target) return;

  const resetLayoutScroll = () => {
    let ancestor = container.parentElement;
    while (ancestor) {
      if (!ancestor.classList.contains("workspace-preview-pane") && ancestor.tagName !== "PRE") {
        if (ancestor.scrollTop !== 0) ancestor.scrollTop = 0;
        if (ancestor.scrollLeft !== 0) ancestor.scrollLeft = 0;
      }
      ancestor = ancestor.parentElement;
    }
    const doc = container.ownerDocument;
    if (doc.documentElement.scrollTop !== 0) doc.documentElement.scrollTop = 0;
    if (doc.documentElement.scrollLeft !== 0) doc.documentElement.scrollLeft = 0;
    if (doc.body && doc.body.scrollTop !== 0) doc.body.scrollTop = 0;
    if (doc.body && doc.body.scrollLeft !== 0) doc.body.scrollLeft = 0;
    const win = doc.defaultView;
    if (win && (win.scrollX !== 0 || win.scrollY !== 0)) {
      win.scrollTo(0, 0);
    }
  };

  const previewPane = container.closest<HTMLElement>(".workspace-preview-pane") ?? container;
  const pre = target.closest<HTMLElement>("pre");
  const scrollContainer = (pre && pre.scrollHeight > pre.clientHeight) ? pre : previewPane;
  if (scrollContainer && typeof scrollContainer.scrollTo === "function") {
    const cRect = scrollContainer.getBoundingClientRect();
    const tRect = target.getBoundingClientRect();
    if (cRect.height > 0) {
      const offset = tRect.top - cRect.top;
      const targetScrollTop = scrollContainer.scrollTop + offset - (cRect.height / 2) + (tRect.height / 2);
      scrollContainer.scrollTop = Math.max(0, targetScrollTop);
    }
  }

  target.scrollIntoView({ block: "center", inline: "nearest" });
  resetLayoutScroll();
  requestAnimationFrame(resetLayoutScroll);

  if (markdown) return;
  const content = target.querySelector<HTMLElement>(".preview-line-code") ?? target;
  const document = container.ownerDocument;
  const range = document.createRange();
  range.selectNodeContents(content);
  if (position.column !== undefined) {
    let remaining = position.column - 1;
    const walker = document.createTreeWalker(content, NodeFilter.SHOW_TEXT);
    let found = false;
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const length = node.textContent?.length ?? 0;
      if (remaining < length) {
        range.setStart(node, remaining);
        range.setEnd(node, remaining + 1);
        found = true;
        break;
      }
      remaining -= length;
    }
    if (!found) range.collapse(false);
  }
  const selection = document.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(range);
  const targetPre = content.closest("pre");
  if (targetPre && position.column !== undefined) {
    const rect = range.getBoundingClientRect();
    const viewport = targetPre.getBoundingClientRect();
    if (rect.right > viewport.right) targetPre.scrollLeft += rect.right - viewport.right;
    else if (rect.left < viewport.left) targetPre.scrollLeft -= viewport.left - rect.left;
  }
  resetLayoutScroll();
  requestAnimationFrame(resetLayoutScroll);
}

function WorkspacePreview({ preview, navigation }: { preview: WorkspaceFilePreview; navigation?: WorkspaceFileOpenRequest | undefined }) {
  const actions = useArtifacts();
  const { url, error } = usePreviewUrl(["image", "pdf", "html"].includes(preview.kind) ? preview.path : undefined, preview.version);
  const [source, setSource] = useState(preview.kind === "html" && navigation?.line !== undefined);
  const [loadError, setLoadError] = useState("");
  const [navigationNotice, setNavigationNotice] = useState<string>();
  const [codeRevision, setCodeRevision] = useState(0);
  const previewRef = useRef<HTMLElement>(null);
  const [findRequested, setFindRequested] = useState(false);
  const [findPreview, setFindPreview] = useState<WorkspaceFilePreview>();
  const [findNotice, setFindNotice] = useState("");
  const requestFileFind = useCallback(() => setFindRequested(true), []);
  useEffect(() => {
    let current = true;
    setFindPreview(undefined); setFindNotice("");
    if (findRequested && preview.truncated && preview.version && actions?.sessionId) {
      setFindNotice("正在读取文件正文…");
      void window.eidosRuntime.readWorkspaceFileForFind({ sessionId: actions.sessionId, path: preview.path, version: preview.version }).then((result) => {
        if (current) { setFindPreview(result); setFindNotice(result.truncated ? "文件超过 2 MiB，仅查找已读取部分" : ""); }
      }).catch((cause) => { if (current) setFindNotice(userFacingError(cause)); });
    }
    return () => { current = false; };
  }, [findRequested, preview.path, preview.version, preview.truncated, actions?.sessionId]);
  const visiblePreview = findPreview ?? preview;
  const codeReady = useCallback(() => setCodeRevision((value) => value + 1), []);
  useEffect(() => {
    if (preview.kind === "html" && navigation?.line !== undefined) setSource(true);
  }, [navigation?.requestId, navigation?.line, preview.kind]);
  useEffect(() => {
    setNavigationNotice(undefined);
    const line = navigation?.line;
    if (!previewRef.current || !navigation || line === undefined || !Number.isSafeInteger(line) || line < 1) return;
    if (navigation.column !== undefined && (!Number.isSafeInteger(navigation.column) || navigation.column < 1)) return;
    if (preview.kind === "html" && !source) return;
    if (!["text", "code", "markdown", "html"].includes(preview.kind)) return;
    const lines = (preview.content ?? "").split("\n");
    if (lines.length > 1 && lines.at(-1) === "") lines.pop();
    if (line > lines.length) {
      setNavigationNotice(preview.truncated ? "目标行超出了当前截断预览。" : "目标行不在当前文件中。");
      return;
    }
    selectPreviewPosition(previewRef.current, navigation, preview.kind === "markdown");
  }, [navigation, preview.content, preview.kind, preview.truncated, source, codeRevision]);
  return (
    <article className="workspace-preview" ref={previewRef} tabIndex={-1} onPointerDown={(event) => { if (!(event.target as Element).closest("button,input,select,textarea,a")) event.currentTarget.focus({ preventScroll: true }); }}>
      {(["text", "code", "markdown"].includes(preview.kind) || preview.kind === "html" && source) && <FileFind key={preview.path} root={previewRef} revision={`${preview.version}:${codeRevision}:${findPreview?.content?.length ?? 0}`} truncated={visiblePreview.truncated} onOpen={requestFileFind} notice={findNotice} />}
      {visiblePreview.truncated && <p className="workspace-preview-notice">预览已截断</p>}
      {navigationNotice && <p className="workspace-preview-notice" role="status">{navigationNotice}</p>}
      {(error || loadError) && <p role="alert">{error || loadError}</p>}
      {preview.kind === "html" && <div className="artifact-toolbar"><button onClick={() => setSource(!source)}>{source ? "查看预览入口" : "查看源码"}</button><button disabled={!url || preview.truncated} onClick={() => { if (url) actions?.openBrowser(url); }}>打开交互预览</button></div>}
      {preview.kind === "image" ? (url ? <img className="artifact-preview-image" src={url} alt={preview.path} onError={() => setLoadError("图片无法加载，请刷新或检查文件格式。")} /> : <p role="status">正在加载图片…</p>)
      : preview.kind === "pdf" ? (
        url ? (
          <PdfViewer
            url={url}
            title={preview.path}
            onOpenExternal={actions?.sessionId ? () => {
              void window.eidosRuntime?.openWorkspacePathInEditor?.(actions.sessionId, preview.path);
            } : undefined}
            onError={(msg) => setLoadError(msg)}
          />
        ) : <p role="status">正在加载 PDF…</p>
      )
      : preview.kind === "html" && !source ? <p>你可以在网页面板中操作页面并检查效果。</p>
      : preview.kind === "unavailable" ? (
        <div className="workspace-preview-unavailable">
          <strong>{preview.reason === "binary" ? "二进制文件无法预览" : "此文件类型暂不支持预览"}</strong>
          <span>请在外部编辑器中打开该文件。</span>
        </div>
      ) : preview.kind === "markdown" ? (
        <div className="workspace-markdown-preview">
          <MarkdownContent content={visiblePreview.content ?? ""} documentPath={preview.path} />
        </div>
      ) : preview.kind === "code" || preview.kind === "html" ? (
        <ShikiPreview
          code={visiblePreview.content ?? ""}
          onReady={codeReady}
          {...(preview.language === undefined ? {} : { language: preview.language })}
        />
      ) : (
        <NumberedText code={visiblePreview.content ?? ""} />
      )}
    </article>
  );
}

function NumberedText({ code }: { code: string }) {
  const lines = useMemo(() => {
    const parts = code.split("\n");
    if (parts.length > 1 && parts[parts.length - 1] === "") parts.pop();
    return parts;
  }, [code]);
  return (
    <pre className="workspace-text-preview workspace-text-preview--numbered">
      {lines.map((line, index) => (
        <span className="preview-line" data-source-line={index + 1} key={index}>
          <span className="preview-line-number" aria-hidden="true">{index + 1}</span>
          <span className="preview-line-code">{line}</span>
        </span>
      ))}
    </pre>
  );
}

function ShikiPreview({ code, language, onReady }: { code: string; language?: string; onReady?: () => void }) {
  const [html, setHtml] = useState<string>();
  useEffect(() => { if (html) onReady?.(); }, [html, onReady]);
  useEffect(() => {
    let active = true;
    setHtml(undefined);
    const targetLang = (language ?? "text").toLowerCase();
    void import("shiki").then(async ({ codeToHtml }) => {
      try {
        const rendered = await codeToHtml(code, {
          lang: targetLang,
          theme: "github-light-default",
          transformers: [{
            name: "eidos-preview-source-lines",
            line(node, line) { node.properties["data-source-line"] = line; },
          }],
        });
        if (active) setHtml(rendered);
      } catch {
        try {
          const fallback = await codeToHtml(code, {
            lang: "text",
            theme: "github-light-default",
            transformers: [{
              name: "eidos-preview-source-lines",
              line(node, line) { node.properties["data-source-line"] = line; },
            }],
          });
          if (active) setHtml(fallback);
        } catch {}
      }
    }).catch(() => {});
    return () => { active = false; };
  }, [code, language]);
  if (!html) return <NumberedText code={code} />;
  return <div className="workspace-code-preview" dangerouslySetInnerHTML={{ __html: html }} />;
}
