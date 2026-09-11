import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export interface ArtifactActions {
  sessionId: string;
  executionRoot: string;
  openFile(path: string): void;
  openBrowser(url: string): void;
  openExternal?: ((path: string) => void) | undefined;
  openReview?: ((request: { runId: string; path?: string; itemId?: string }) => void) | undefined;
}
const Context = createContext<ArtifactActions | undefined>(undefined);
export const ArtifactProvider = Context.Provider;
export const useArtifacts = () => useContext(Context);

/** Resolve links against the displayed document; Runtime performs the authoritative read. */
export function artifactPath(href: string, root: string, documentPath?: string): string | undefined {
  try {
    if (!href || href.startsWith("#")) return undefined;
    let value = href.startsWith("file://") ? href : decodeURIComponent(href);
    if (/^[a-z][a-z\d+.-]*:/i.test(value) && !value.startsWith("file://")) return undefined;
    if (value.startsWith("file://")) {
      const url = new URL(value);
      if (url.hostname) return undefined;
      value = decodeURIComponent(url.pathname);
    }
    value = value.replace(/#.*$/, "").replace(/:\d+(?::\d+)?$/, "");
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

export function usePreviewUrl(path: string | undefined, version?: string) {
  const actions = useArtifacts();
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!actions || !path || version) return;
    return window.eidosRuntime.onNotification((event) => {
      if (event.method === "workspace/changed" && event.params.sessionId === actions.sessionId && event.params.paths.some((changed) => changed === path || path.startsWith(changed + "/"))) setRevision((value) => value + 1);
    });
  }, [actions?.sessionId, path, version]);
  const [state, setState] = useState<{ url?: string; error?: string }>({});
  useEffect(() => {
    let current = true;
    let created: string | undefined;
    setState({});
    if (!actions || !path) return;
    void window.eidosRuntime.prepareWorkspacePreview(actions.sessionId, path, version).then((url) => {
      created = url;
      if (current) setState({ url });
      else void window.eidosRuntime.releaseWorkspacePreview(url).catch(() => {});
    }).catch(() => { if (current) setState({ error: "预览不可用，请刷新文件。" }); });
    return () => { current = false; if (created) void window.eidosRuntime.releaseWorkspacePreview(created).catch(() => {}); };
  }, [actions?.sessionId, actions?.executionRoot, path, version, revision]);
  return state;
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
  const path = artifactPath(href, actions.executionRoot, documentPath);
  const web = /^https?:\/\//i.test(href);
  if (!path && !web) return <span title={href}>{children}</span>;
  return <a href={href} onClick={(event) => { event.preventDefault(); if (web) actions.openBrowser(href); else if (path) actions.openFile(path); }}>{children}</a>;
}
