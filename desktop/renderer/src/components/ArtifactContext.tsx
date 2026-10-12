import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { getOfficialFileIcon } from "./FileIcons.js";

export interface WorkspaceFilePosition {
  line?: number;
  column?: number;
}

export interface WorkspaceFileLink extends WorkspaceFilePosition {
  path: string;
}

export interface ArtifactActions {
  sessionId: string;
  executionRoot: string;
  projectId?: string;
  openFile(path: string, position?: WorkspaceFilePosition): void;
  openBrowser(url: string): void;
  openExternal?: ((path: string) => void | Promise<void>) | undefined;
  openReview?: ((request: { runId: string; path?: string; itemId?: string }) => void) | undefined;
  showInFinder?: ((path: string) => void | Promise<void>) | undefined;
}
const Context = createContext<ArtifactActions | undefined>(undefined);
export const ArtifactProvider = Context.Provider;
export const useArtifacts = () => useContext(Context);

/** Resolve links against the displayed document; Runtime performs the authoritative read. */
export function artifactPath(href: string, root: string, documentPath?: string, literal = false): string | undefined {
  try {
    if (!href || (!literal && href.startsWith("#"))) return undefined;
    let value = literal || href.startsWith("file://") ? href : decodeURIComponent(href);
    if (!literal && /^[a-z][a-z\d+.-]*:/i.test(value) && !value.startsWith("file://")) return undefined;
    if (!literal && value.startsWith("file://")) {
      const url = new URL(value);
      if (url.hostname) return undefined;
      value = decodeURIComponent(url.pathname);
    }
    if (!literal) value = value.replace(/#.*$/, "").replace(/:\d+(?::\d+)?$/, "");
    if (value.startsWith("/")) {
      if (!value.startsWith(root.replace(/\/$/, "") + "/")) return undefined;
      value = value.slice(root.replace(/\/$/, "").length + 1);
    } else if (documentPath) value = documentPath.split("/").slice(0, -1).concat(value).join("/");
    const parts: string[] = [];
    for (const part of value.split("/")) {
      if (part === "..") { if (!parts.length) return undefined; parts.pop(); }
      else if (part && part !== ".") parts.push(part);
    }
    return parts.length && !value.includes("\0") ? parts.join("/") : undefined;
  } catch { return undefined; }
}

/** Conversation links require an absolute target; document links retain their local base. */
export function workspaceFileLink(href: string, root: string, documentPath?: string): WorkspaceFileLink | undefined {
  try {
    if (!href || href.startsWith("#")) return undefined;
    let value = href;
    if (value.startsWith("file://")) {
      if (!documentPath) return undefined;
      const url = new URL(value);
      if (url.hostname) return undefined;
      value = url.pathname + url.hash;
    }
    const hashIndex = value.indexOf("#");
    const fragment = hashIndex < 0 ? undefined : value.slice(hashIndex + 1);
    if (hashIndex >= 0) value = value.slice(0, hashIndex);
    const fragmentPosition = fragment?.match(/^L(\d+)(?:C(\d+))?$/);
    if (fragment !== undefined && !fragmentPosition && !documentPath) return undefined;
    const suffixPosition = value.match(/:(\d+)(?::(\d+))?$/);
    if (suffixPosition && fragmentPosition) return undefined;
    if (suffixPosition) value = value.slice(0, -suffixPosition[0].length);
    const position = suffixPosition ?? fragmentPosition;
    const line = position ? Number(position[1]) : undefined;
    const column = position?.[2] === undefined ? undefined : Number(position[2]);
    if (line !== undefined && (!Number.isSafeInteger(line) || line < 1)) return undefined;
    if (column !== undefined && (!Number.isSafeInteger(column) || column < 1)) return undefined;
    value = decodeURIComponent(value);
    if (/^[a-z][a-z\d+.-]*:/i.test(value) || value.startsWith("//")) return undefined;
    if (!documentPath && !value.startsWith("/")) return undefined;
    const path = artifactPath(value, root, documentPath, true);
    return path && path.length <= 4096 && !/[\u0000-\u001f\u007f]/.test(path) ? {
      path,
      ...(line === undefined ? {} : { line }),
      ...(column === undefined ? {} : { column }),
    } : undefined;
  } catch { return undefined; }
}

export function usePreviewUrl(path: string | undefined, version?: string) {
  const actions = useArtifacts();
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!actions || !path || version || typeof window.eidosRuntime?.onNotification !== "function") return;
    return window.eidosRuntime.onNotification((event) => {
      if (event.method === "workspace/changed" && event.params.sessionId === actions.sessionId && event.params.paths.some((changed) => changed === path || path.startsWith(changed + "/"))) setRevision((value) => value + 1);
    });
  }, [actions?.sessionId, path, version]);
  const key = JSON.stringify([actions?.sessionId, actions?.executionRoot, actions?.projectId, path, version, revision]);
  const [state, setState] = useState<{ key?: string; url?: string; error?: string }>({});
  useEffect(() => {
    let current = true;
    let created: string | undefined;
    setState({});
    if (!actions || !path || typeof window.eidosRuntime?.prepareWorkspacePreview !== "function") return;
    void window.eidosRuntime.prepareWorkspacePreview(
      actions.sessionId,
      path,
      version,
      actions.executionRoot,
      actions.projectId,
    ).then((url) => {
      created = url;
      if (current) setState({ key, url });
      else if (typeof window.eidosRuntime?.releaseWorkspacePreview === "function") void window.eidosRuntime.releaseWorkspacePreview(url).catch(() => {});
    }).catch(() => { if (current) setState({ key, error: "预览不可用，请重新打开文件。" }); });
    return () => {
      current = false;
      if (created && typeof window.eidosRuntime?.releaseWorkspacePreview === "function") {
        void window.eidosRuntime.releaseWorkspacePreview(created).catch(() => {});
      }
    };
  }, [actions?.sessionId, actions?.executionRoot, actions?.projectId, path, version, revision, key]);
  return state.key === key ? state : {};
}

export function ArtifactImage({ path, alt, version }: { path: string; alt: string; version?: string }) {
  const { url, error } = usePreviewUrl(path, version);
  const actions = useArtifacts();
  return url ? <button type="button" className="artifact-image-button" onClick={() => actions?.openFile(path)} aria-label={`打开 ${alt || path}`}><img src={url} alt={alt} loading="lazy" onError={(event) => { event.currentTarget.alt = "图片加载失败，请刷新文件。"; }} /></button>
    : <span role="status">{error || `正在读取 ${alt || path}…`}</span>;
}

export function ArtifactLink({ href, documentPath, children }: { href?: string; documentPath?: string; children: ReactNode }) {
  const actions = useArtifacts();
  if (!href || !actions) return <span>{children}</span>;
  const web = /^https?:\/\//i.test(href);
  if (web) return <a href={href} onClick={(event) => { event.preventDefault(); actions.openBrowser(href); }}>{children}</a>;
  const target = workspaceFileLink(href, actions.executionRoot, documentPath);
  if (!target) return <span title={href}>{children}</span>;
  const title = `在工作区中打开 ${target.path}${target.line ? ` (第 ${target.line} 行${target.column ? `，第 ${target.column} 列` : ""})` : ""}`;
  return (
    <a href={href} className={documentPath ? undefined : "markdown-file-link"} title={title}
      onClick={(event) => {
        event.preventDefault();
        const { path, ...position } = target;
        if (position.line === undefined && position.column === undefined) actions.openFile(path);
        else actions.openFile(path, position);
      }}>
      {documentPath ? children : <>
        <span className="markdown-file-icon" aria-hidden="true">{getOfficialFileIcon(target.path)}</span>
        <span className="markdown-file-text">{children}</span>
      </>}
    </a>
  );
}
