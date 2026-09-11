import { BrowserWindow, WebContentsView, protocol, session, webContents, type WebContents, type Session } from "electron";
import { randomUUID } from "node:crypto";
import type { RuntimeClient } from "./runtime-client.js";
import type { BrowserAnnotation, BrowserBounds, BrowserPageState } from "../shared/index.js";

protocol.registerSchemesAsPrivileged([{ scheme: "eidos-preview", privileges: { standard: true, secure: true, supportFetchAPI: true } }]);

interface PreviewGrant { owner: number; sessionId: string; root: string; path: string; workspaceVersion: string; version?: string }
interface BrowserEntry { owner: number; sessionId: string; browserId: string; root: string; view: WebContentsView; error?: string | undefined; token?: string | undefined }

/** Desktop views own no file authority: every resource is read through Runtime. */
export class ArtifactPreviewManager {
  private grants = new Map<string, PreviewGrant>();
  private browsers = new Map<string, BrowserEntry>();
  private generation = new Map<string, number>();
  constructor(private client: () => RuntimeClient) {}

  install(): void {
    session.defaultSession.protocol.handle("eidos-preview", (request) => this.resource(request, false));
  }

  private async root(sessionId: string): Promise<string> {
    const { session: current } = await this.client().readSession(sessionId);
    const root = current.executionMode === "worktree" ? current.worktree?.worktreeRoot : current.workspaceRoot;
    if (!root) throw new Error("工作目录当前不可用。");
    return root;
  }

  async prepare(owner: WebContents, sessionId: string, path: string, version?: string): Promise<string> {
    if (this.grants.size >= 256) throw new Error("打开的预览过多，请先关闭部分文件。");
    const root = await this.root(sessionId);
    const first = await this.client().readWorkspaceAsset(sessionId, path, root, 0, version);
    if (owner.isDestroyed()) throw new Error("窗口已关闭。");
    const token = randomUUID();
    this.grants.set(token, { owner: owner.id, sessionId, root, path, workspaceVersion: first.workspaceVersion, version: first.version });
    return `eidos-preview://${token}/${path.split("/").map(encodeURIComponent).join("/")}`;
  }

  release(owner: WebContents, url: string): void {
    const token = new URL(url).hostname;
    if (this.grants.get(token)?.owner === owner.id) this.grants.delete(token);
  }

  private async resource(request: Request, executable: boolean, allowedToken?: string): Promise<Response> {
    try {
      const url = new URL(request.url);
      const grant = this.grants.get(url.hostname);
      if (!grant || (allowedToken && allowedToken !== url.hostname) || request.method !== "GET") return new Response(null, { status: 403 });
      const path = decodeURIComponent(url.pathname.slice(1));
      // Passive previews can only load the selected asset. Browser grants permit its workspace resources.
      if (!executable && path !== grant.path) return new Response(null, { status: 403 });
      let chunk = await this.client().readWorkspaceAsset(grant.sessionId, path, grant.root, 0, path === grant.path ? grant.version : undefined, grant.workspaceVersion);
      if (!executable && !chunk.mimeType.startsWith("image/") && chunk.mimeType !== "application/pdf") return new Response(null, { status: 415 });
      let done = false;
      const stream = new ReadableStream<Uint8Array>({
        pull: async (controller) => {
          if (done || request.signal.aborted || !this.grants.has(url.hostname)) { controller.close(); return; }
          try {
            controller.enqueue(Buffer.from(chunk.data, "base64"));
            if (chunk.complete) { done = true; controller.close(); return; }
            const offset = chunk.nextOffset;
            chunk = await this.client().readWorkspaceAsset(grant.sessionId, path, grant.root, offset, chunk.version, grant.workspaceVersion);
            if (chunk.nextOffset <= offset && !chunk.complete) throw new Error("文件读取没有进展。");
          } catch (error) { done = true; controller.error(error); }
        },
        cancel: () => { done = true; },
      });
      return new Response(stream, { headers: {
        "Content-Type": chunk.mimeType,
        "Content-Length": String(chunk.sizeBytes),
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": executable
          ? "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; frame-src 'none'; base-uri 'self'; form-action 'none'"
          : "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox",
      } });
    } catch { return new Response("文件已变化、不可用或超出预览限制，请刷新。", { status: 409 }); }
  }

  private key(owner: WebContents, sessionId: string, browserId: string): string { return `${owner.id}\0${sessionId}\0${browserId}`; }

  private configure(ses: Session, entry: BrowserEntry): void {
    ses.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    ses.setPermissionCheckHandler(() => false);
    ses.on("will-download", (event) => event.preventDefault());
    ses.protocol.handle("eidos-preview", (request) => this.resource(request, true, entry.token ?? "denied"));
    ses.webRequest.onBeforeRequest((details, callback) => {
      const target = new URL(details.url);
      const allowed = entry.token
        ? target.protocol === "eidos-preview:" && target.hostname === entry.token || ["data:", "blob:"].includes(target.protocol)
        : ["http:", "https:", "ws:", "wss:", "data:", "blob:"].includes(target.protocol);
      callback({ cancel: !allowed });
    });
  }

  async open(owner: WebContents, sessionId: string, browserId: string, target: string): Promise<BrowserPageState> {
    const key = this.key(owner, sessionId, browserId);
    const generation = (this.generation.get(key) ?? 0) + 1;
    this.generation.set(key, generation);
    const root = await this.root(sessionId);
    if (owner.isDestroyed() || this.generation.get(key) !== generation) throw new Error("页面请求已取消。");
    let entry = this.browsers.get(key);
    if (entry && entry.root !== root) { this.close(owner, sessionId, browserId); this.generation.set(key, generation); entry = undefined; }
    const parsed = new URL(target);
    if (!["http:", "https:", "eidos-preview:"].includes(parsed.protocol) || parsed.username || parsed.password) throw new Error("仅支持网页地址或当前文件预览。");
    if (parsed.protocol === "eidos-preview:") {
      const grant = this.grants.get(parsed.hostname);
      if (!grant || grant.owner !== owner.id || grant.sessionId !== sessionId || grant.root !== root) throw new Error("文件预览已失效。");
    }
    if (!entry) {
      if (this.browsers.size >= 8) throw new Error("打开的网页过多。");
      const window = BrowserWindow.fromWebContents(owner);
      if (!window) throw new Error("窗口已关闭。");
      const view = new WebContentsView({ webPreferences: { partition: `eidos-preview-${randomUUID()}`, nodeIntegration: false, contextIsolation: true, sandbox: true, plugins: true } });
      entry = { owner: owner.id, sessionId, browserId, root, view };
      this.configure(view.webContents.session, entry);
      const current = entry;
      view.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
      view.webContents.on("will-navigate", (event, url) => {
        const next = new URL(url);
        if (current.token ? next.protocol !== "eidos-preview:" || next.hostname !== current.token : !["http:", "https:"].includes(next.protocol)) event.preventDefault();
      });
      view.webContents.on("did-fail-load", (_event, code, _description, _url, mainFrame) => {
        if (mainFrame && code !== -3) current.error = "页面未能加载，请确认地址和开发服务状态。";
      });
      window.contentView.addChildView(view);
      view.setVisible(false);
      this.browsers.set(key, entry);
    }
    if (parsed.protocol === "eidos-preview:") {
      const source = this.grants.get(parsed.hostname)!;
      const path = decodeURIComponent(parsed.pathname.slice(1));
      const refreshed = await this.client().readWorkspaceAsset(sessionId, path, root, 0, entry.token === parsed.hostname || path !== source.path ? undefined : source.version, source.workspaceVersion);
      if (owner.isDestroyed() || this.generation.get(key) !== generation) throw new Error("页面请求已取消。");
      const token = randomUUID();
      this.grants.set(token, { ...source, path, version: refreshed.version });
      if (entry.token) this.grants.delete(entry.token);
      entry.token = token;
      target = `eidos-preview://${token}/${path.split("/").map(encodeURIComponent).join("/")}${parsed.search}${parsed.hash}`;
    } else {
      if (entry.token) this.grants.delete(entry.token);
      entry.token = undefined;
    }
    entry.error = undefined;
    try { await entry.view.webContents.loadURL(target); } catch { entry.error = "页面未能加载，请确认地址和开发服务状态。"; }
    return this.state(owner, sessionId, browserId);
  }

  state(owner: WebContents, sessionId: string, browserId: string): BrowserPageState {
    const entry = this.browsers.get(this.key(owner, sessionId, browserId));
    if (!entry) return { url: "", title: "", loading: false };
    return { url: entry.view.webContents.getURL(), title: entry.view.webContents.getTitle(), loading: entry.view.webContents.isLoading(), ...(entry.error ? { error: entry.error } : {}) };
  }

  bounds(owner: WebContents, sessionId: string, browserId: string, bounds: BrowserBounds | null): void {
    const entry = this.browsers.get(this.key(owner, sessionId, browserId));
    if (!entry) return;
    if (!bounds) { entry.view.setVisible(false); return; }
    const window = BrowserWindow.fromWebContents(owner);
    if (!window) return;
    const [contentWidth, contentHeight] = window.getContentSize();
    const width = contentWidth ?? 0;
    const height = contentHeight ?? 0;
    const x = Math.max(0, Math.min(width, Math.round(bounds.x)));
    const y = Math.max(0, Math.min(height, Math.round(bounds.y)));
    entry.view.setBounds({ x, y, width: Math.max(0, Math.min(width - x, Math.round(bounds.width))), height: Math.max(0, Math.min(height - y, Math.round(bounds.height))) });
    entry.view.setVisible(bounds.width > 0 && bounds.height > 0);
  }

  async annotate(owner: WebContents, sessionId: string, browserId: string): Promise<BrowserAnnotation> {
    const entry = this.browsers.get(this.key(owner, sessionId, browserId));
    if (!entry || await this.root(sessionId) !== entry.root) throw new Error("页面已失效。");
    const wc = entry.view.webContents;
    const url = wc.getURL();
    const selection: unknown = await wc.executeJavaScript("String(window.getSelection()?.toString() || '').slice(0, 4096)");
    const elements: unknown = await wc.executeJavaScript(`Array.from(document.querySelectorAll('button,a,input,textarea,select,h1,h2,h3,p,label,img,canvas,svg')).slice(0, 2000).map(el => { const r=el.getBoundingClientRect(); return {tag:el.tagName.toLowerCase(),text:String(el.textContent || el.getAttribute('aria-label') || el.getAttribute('alt') || '').trim().slice(0,256),x:r.x/innerWidth*100,y:r.y/innerHeight*100,width:r.width/innerWidth*100,height:r.height/innerHeight*100}; }).filter(r => r.width>0 && r.height>0 && r.x<100 && r.y<100 && r.x+r.width>0 && r.y+r.height>0).slice(0,100)`);
    const screenshot = (await wc.capturePage()).resize({ width: 1280 }).toDataURL();
    if (url !== wc.getURL()) throw new Error("页面已切换，请重新标注。");
    return { elements: Array.isArray(elements) ? elements.filter(isAnnotationElement).slice(0, 100) : [], url, title: wc.getTitle().slice(0, 512), selection: typeof selection === "string" ? selection : "", screenshot, capturedAt: Date.now() };
  }

  close(owner: WebContents, sessionId: string, browserId: string): void {
    const key = this.key(owner, sessionId, browserId);
    this.generation.set(key, (this.generation.get(key) ?? 0) + 1);
    const entry = this.browsers.get(key);
    if (!entry) return;
    this.browsers.delete(key);
    if (entry.token) this.grants.delete(entry.token);
    if (!owner.isDestroyed()) BrowserWindow.fromWebContents(owner)?.contentView.removeChildView(entry.view);
    entry.view.webContents.close();
  }
  closeSession(sessionId: string): void {
    for (const [key, generation] of this.generation) if (key.split("\0")[1] === sessionId) this.generation.set(key, generation + 1);
    for (const entry of [...this.browsers.values()]) {
      const owner = webContents.fromId(entry.owner);
      if (entry.sessionId === sessionId && owner) this.close(owner, sessionId, entry.browserId);
    }
    for (const [token, grant] of this.grants) if (grant.sessionId === sessionId) this.grants.delete(token);
  }
  closeOwner(owner: WebContents): void {
    for (const entry of [...this.browsers.values()]) if (entry.owner === owner.id) this.close(owner, entry.sessionId, entry.browserId);
    for (const [token, grant] of this.grants) if (grant.owner === owner.id) this.grants.delete(token);
  }
  closeAll(): void {
    for (const entry of this.browsers.values()) entry.view.webContents.close();
    this.browsers.clear(); this.grants.clear(); this.generation.clear();
  }
}

function isAnnotationElement(value: unknown): value is import("../shared/index.js").BrowserAnnotationElement {
  if (!value || typeof value !== "object") return false;
  return typeof Reflect.get(value, "tag") === "string" && typeof Reflect.get(value, "text") === "string"
    && ["x", "y", "width", "height"].every((key) => typeof Reflect.get(value, key) === "number" && Number.isFinite(Reflect.get(value, key)));
}
