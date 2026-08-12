import { useCallback, useEffect, useRef, useState } from "react";
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
  return entries.map((entry) => {
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
  listDirectory = defaultListDirectory,
  readPreview = defaultReadPreview,
  subscribeChanges = defaultSubscribeChanges,
}: WorkspaceExplorerProps) {
  const [nodes, setNodes] = useState<WorkspaceTreeNode[]>([]);
  const [rootTruncated, setRootTruncated] = useState(false);
  const [loadingRoot, setLoadingRoot] = useState(true);
  const [loadingPath, setLoadingPath] = useState<string>();
  const [preview, setPreview] = useState<WorkspaceFilePreview>();
  const [error, setError] = useState<string>();
  const requestVersion = useRef(0);

  useEffect(() => {
    const version = ++requestVersion.current;
    setNodes([]);
    setPreview(undefined);
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
  }, [sessionId, listDirectory]);

  const loadDirectory = useCallback((path: string, force = false) => {
    const node = findNode(nodes, path);
    if (
      path !== "."
      && (!node || node.kind !== "directory" || (node.loaded && !force))
    ) return;
    if (loadingPath === path) return;
    setLoadingPath(path);
    setError(undefined);
    void listDirectory(sessionId, path)
      .then((listing) => {
        if (path === ".") {
          setNodes((current) => toNodes(listing.entries, current));
          setRootTruncated(listing.truncated);
        } else {
          setNodes((current) => replaceDirectoryChildren(current, path, listing.entries));
        }
        if (listing.truncated) setError(`${path} 的目录内容已截断`);
      })
      .catch((cause: unknown) => {
        setError(cause instanceof Error ? cause.message : "Workspace 目录读取失败");
      })
      .finally(() => setLoadingPath((current) => current === path ? undefined : current));
  }, [listDirectory, loadingPath, nodes, sessionId]);

  const openFile = useCallback((path: string) => {
    setLoadingPath(path);
    setError(undefined);
    void readPreview(sessionId, path)
      .then(setPreview)
      .catch((cause: unknown) => {
        setError(cause instanceof Error ? cause.message : "文件预览读取失败");
      })
      .finally(() => setLoadingPath((current) => current === path ? undefined : current));
  }, [readPreview, sessionId]);

  useEffect(() => subscribeChanges(sessionId, (paths) => {
    const parents = new Set(paths.map((path) => {
      const slash = path.lastIndexOf("/");
      return slash < 0 ? "." : path.slice(0, slash);
    }));
    for (const parent of parents) {
      if (parent === "." || findNode(nodes, parent)?.loaded) {
        loadDirectory(parent, true);
      }
    }
    if (preview && paths.includes(preview.path)) openFile(preview.path);
  }), [loadDirectory, nodes, openFile, preview, sessionId, subscribeChanges]);

  const renderNode = useCallback((props: NodeRendererProps<WorkspaceTreeNode>) => (
    <WorkspaceTreeRow
      {...props}
      loading={loadingPath === props.node.id}
      onOpenDirectory={loadDirectory}
      onOpenFile={openFile}
    />
  ), [loadDirectory, loadingPath, openFile]);

  return (
    <section className="workspace-explorer" aria-label="Workspace 文件浏览器">
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
          <div className="workspace-tree-scroll">
            <Tree<WorkspaceTreeNode>
              data={nodes}
              width="100%"
              height={720}
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
      <div className="workspace-preview-pane" aria-live="polite">
        {loadingPath && !preview ? (
          <p className="workspace-preview-placeholder" role="status">正在读取文件…</p>
        ) : preview ? (
          <WorkspacePreview preview={preview} />
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
      onClick={() => node.select()}
      onDoubleClick={() => {
        if (node.data.kind === "directory") {
          onOpenDirectory(node.id);
          node.toggle();
        } else {
          onOpenFile(node.id);
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
