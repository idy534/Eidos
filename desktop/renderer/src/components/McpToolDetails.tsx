import { createContext, useContext, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import type { Item, ToolAttachment, ToolCall } from "../../../shared/index.js";
import { CloseIcon } from "./MenuIcons.js";
import { ToolTextView } from "./ToolTextView.js";

/** pluginId → plugin display name, provided where the plugin list is loaded. */
export const McpPluginNamesContext = createContext<ReadonlyMap<string, string>>(new Map());

const BUNDLED_BROWSER_PLUGIN = "eidos-browser";
const TEXT_PREVIEW_LINES = 12;
const ERROR_PREVIEW_LINES = 6;
const ARGUMENT_PREVIEW_COUNT = 4;
const SHOW_DURATION_FROM_MS = 2_000;
const SECRET_FIELD = /密码|口令|password|passcode|passwd|pin\b|secret|token/i;

const ERROR_MESSAGES: Record<string, string> = {
  mcp_connection_lost: "MCP 服务器连接中断",
  mcp_unavailable: "MCP 服务器暂不可用",
  mcp_startup_timeout: "MCP 服务器启动超时",
  mcp_login_required: "MCP 服务器需要登录，请在设置中完成授权",
  mcp_sandbox_unavailable: "无法为 MCP 服务器创建沙盒",
  mcp_image_invalid: "返回的图像无效",
  mcp_protocol_error: "MCP 服务器返回了无法识别的响应",
  mcp_tool_error: "MCP 服务器报告了错误",
  mcp_tool_canceled: "已取消",
  mcp_tool_timeout: "MCP 工具执行超时",
  mcp_result_too_large: "返回内容超过 10 MB 上限",
  mcp_content_unsupported: "返回了 Eidos 暂不支持的内容类型",
  TOOL_RESULT_CONTRACT_VIOLATION: "返回的结构化结果不符合工具声明的格式",
  browser_unavailable: "未找到 Google Chrome，请先安装",
  browser_profile_in_use: "浏览器配置正被其他程序占用",
  browser_runtime_not_ready: "浏览器组件尚未下载",
  browser_runtime_unavailable: "浏览器组件不可用",
  browser_start_failed: "Eidos 浏览器启动失败",
  browser_start_canceled: "已取消启动浏览器",
  browser_upload_not_a_file: "只能上传普通文件",
  browser_attach_unavailable: "无法连接你的 Chrome，请确认已开启远程调试",
  browser_upload_outside_workspace: "只能上传工作目录中的文件",
};

/** A readable message for an Eidos MCP error code; unknown codes fall back to the code. */
export function mcpErrorText(code: string): string {
  return ERROR_MESSAGES[code] ?? code;
}

function parseObject(value: string | undefined): Record<string, unknown> {
  if (!value) return {};
  try {
    const parsed: unknown = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function firstLines(value: string, count: number): { shown: string; more: boolean } {
  const lines = value.split("\n");
  return { shown: lines.slice(0, count).join("\n"), more: lines.length > count };
}

export function toolAttachments(result: Record<string, unknown>): ToolAttachment[] {
  const values = Array.isArray(result.attachments) ? result.attachments : [];
  return values.flatMap((value) => {
    const entry = record(value);
    if (entry.kind !== "image" || !Number.isSafeInteger(entry.index) || (entry.index as number) < 0) return [];
    return [{
      index: entry.index as number, kind: "image" as const,
      ...(typeof entry.mime === "string" ? { mime: entry.mime } : {}),
      ...(typeof entry.bytes === "number" ? { bytes: entry.bytes } : {}),
    }];
  }).slice(0, 32);
}

/** "浏览器" for the bundled plugin, its installed name otherwise, then the raw server id. */
export function useMcpSourceName(toolCall: ToolCall): string {
  const names = useContext(McpPluginNamesContext);
  return mcpSourceName(toolCall, names);
}

export function mcpSourceName(toolCall: ToolCall, names: ReadonlyMap<string, string>): string {
  const provenance = toolCall.provenance;
  if (provenance?.pluginId === BUNDLED_BROWSER_PLUGIN) return "浏览器";
  return (provenance?.pluginId && names.get(provenance.pluginId))
    || provenance?.serverId
    || /^mcp__([^_]+(?:_[^_]+)*?)__/.exec(toolCall.toolName)?.[1]
    || "MCP";
}

/** The MCP tool's own name without the "mcp__server__" prefix. */
export function mcpToolName(toolCall: ToolCall): string {
  const serverId = toolCall.provenance?.serverId;
  if (serverId && toolCall.toolName.startsWith(`mcp__${serverId}__`)) return toolCall.toolName.slice(`mcp__${serverId}__`.length);
  return toolCall.toolName.replace(/^mcp__.+?__/, "");
}

/** "GitHub · Create issue · Fix login" for a third-party MCP tool. */
export function mcpToolTitle(toolCall: ToolCall, names: ReadonlyMap<string, string>, status: Item["status"]): string {
  const args = parseObject(toolCall.argumentsJson);
  const subject = Object.values(args).find((value): value is string => typeof value === "string" && value.trim().length > 0);
  const label = [
    mcpSourceName(toolCall, names),
    toolCall.provenance?.displayTitle || mcpToolName(toolCall),
    subject ? subject.replace(/\s+/g, " ").slice(0, 40) : "",
  ].filter(Boolean).join(" · ");
  if (status === "in_progress") return `正在调用 ${label}`;
  return label;
}

function formatSeconds(durationMs: number): string {
  const seconds = durationMs / 1_000;
  return seconds < 10 ? `${seconds.toFixed(1)} 秒` : `${Math.round(seconds)} 秒`;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** One saved tool image, read through Runtime with a lightbox on click. */
export function ToolImage({ sessionId, toolCallId, attachment, name }: {
  sessionId: string;
  toolCallId: string;
  attachment: ToolAttachment;
  name: string;
}) {
  const [state, setState] = useState<{ url?: string; error?: boolean }>({});
  const [attempt, setAttempt] = useState(0);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    let active = true;
    let created: string | undefined;
    setState({});
    if (typeof window.eidosRuntime?.prepareToolContentPreview !== "function") {
      setState({ error: true });
      return;
    }
    void window.eidosRuntime.prepareToolContentPreview(sessionId, toolCallId, attachment.index)
      .then((url) => {
        created = url;
        if (active) setState({ url });
        else void window.eidosRuntime?.releaseInputPreview?.(url);
      })
      .catch(() => { if (active) setState({ error: true }); });
    return () => {
      active = false;
      if (created) void window.eidosRuntime?.releaseInputPreview?.(created);
    };
  }, [sessionId, toolCallId, attachment.index, attempt]);
  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [open]);
  const details = [attachment.mime === "image/jpeg" ? "JPEG" : attachment.mime === "image/png" ? "PNG" : "", attachment.bytes ? formatBytes(attachment.bytes) : ""]
    .filter(Boolean).join(" · ");
  return (
    <div className="tool-image-preview-card">
      {state.url ? (
        <button type="button" className="tool-image-thumbnail-btn" onClick={() => setOpen(true)}
          title={details ? `${name} · ${details}` : name} aria-label={`放大查看${name}`}>
          <img src={state.url} alt={name} loading="lazy" onError={() => setState({ error: true })} />
        </button>
      ) : state.error ? (
        <p className="tool-image-empty" role="status">
          {name}无法加载。<button type="button" className="mcp-inline-action" onClick={() => setAttempt((value) => value + 1)}>重试</button>
        </p>
      ) : (
        <div className="tool-image-loading"><span>正在加载{name}…</span></div>
      )}
      {open && state.url && createPortal(
        <div className="artifact-image-preview" role="dialog" aria-modal="true" aria-label={`${name}预览`}
          onClick={(event) => { if (event.target === event.currentTarget) setOpen(false); }}>
          <button type="button" className="artifact-image-preview__close" aria-label="关闭图片预览" autoFocus onClick={() => setOpen(false)}>
            <CloseIcon />
          </button>
          <img src={state.url} alt={name} />
        </div>,
        document.body,
      )}
    </div>
  );
}

export function ToolImages({ item, toolCall, attachments, name }: {
  item: Item; toolCall: ToolCall; attachments: readonly ToolAttachment[]; name: string;
}) {
  if (attachments.length === 0) return null;
  return (
    <div className="mcp-images">
      {attachments.map((attachment, position) => (
        <ToolImage key={attachment.index} sessionId={item.sessionId} toolCallId={toolCall.id} attachment={attachment}
          name={attachments.length > 1 ? `${name} ${position + 1}` : name} />
      ))}
    </div>
  );
}

function ExpandableText({ value, lines, className }: { value: string; lines: number; className?: string }) {
  const [expanded, setExpanded] = useState(false);
  const { shown, more } = firstLines(value, lines);
  return (
    <div className={className}>
      <pre className="mcp-text">{expanded ? value : shown}</pre>
      {more && (
        <button type="button" className="mcp-inline-action" onClick={() => setExpanded((current) => !current)}>
          {expanded ? "收起" : "展开全部"}
        </button>
      )}
    </div>
  );
}

function argumentValue(value: unknown): string {
  const shown = typeof value === "string" ? value : JSON.stringify(value);
  return shown.length > 200 ? `${shown.slice(0, 200)}…` : shown;
}

function McpArguments({ toolCall }: { toolCall: ToolCall }) {
  const [expanded, setExpanded] = useState(false);
  const args = parseObject(toolCall.argumentsJson);
  const entries = Object.entries(args);
  if (entries.length === 0) return null;
  return (
    <section className="mcp-section" aria-label="参数">
      <p className="mcp-section-label">参数</p>
      {expanded ? (
        <pre className="mcp-text">{JSON.stringify(args, null, 2)}</pre>
      ) : (
        <dl className="mcp-arguments">
          {entries.slice(0, ARGUMENT_PREVIEW_COUNT).map(([key, value]) => (
            <div key={key}><dt>{key}</dt><dd>{argumentValue(value)}</dd></div>
          ))}
        </dl>
      )}
      {(entries.length > ARGUMENT_PREVIEW_COUNT || entries.some(([, value]) => argumentValue(value).endsWith("…"))) && (
        <button type="button" className="mcp-inline-action" onClick={() => setExpanded((current) => !current)}>
          {expanded ? "收起参数" : "显示全部参数"}
        </button>
      )}
    </section>
  );
}

export function McpErrorNotice({ code, detail }: { code: string; detail?: string | undefined }) {
  return (
    <div className="mcp-error" role="alert">
      <p className="tool-summary--error">{mcpErrorText(code)}</p>
      {detail && <ExpandableText value={detail} lines={ERROR_PREVIEW_LINES} className="mcp-error-detail" />}
    </div>
  );
}

/** Source, duration and uncertainty; the internal ids stay in the tooltip. */
export function McpFooter({ toolCall, result }: { toolCall: ToolCall; result: Record<string, unknown> }) {
  const source = useMcpSourceName(toolCall);
  const bundled = toolCall.provenance?.pluginId === BUNDLED_BROWSER_PLUGIN;
  const duration = toolCall.completedAt === undefined ? undefined : Math.max(0, toolCall.completedAt - toolCall.startedAt);
  const uncertain = result.reconciliationRequired === true || result.outcome === "interrupted";
  const parts = [
    `${source}（${bundled ? "内置" : "插件"}）`,
    duration !== undefined && duration >= SHOW_DURATION_FROM_MS ? `耗时 ${formatSeconds(duration)}` : "",
  ].filter(Boolean);
  const ids = [
    toolCall.provenance?.pluginId && `插件 ${toolCall.provenance.pluginId}`,
    toolCall.provenance?.serverId && `服务器 ${toolCall.provenance.serverId}`,
    `工具 ${mcpToolName(toolCall)}`,
  ].filter(Boolean).join(" · ");
  return (
    <>
      {uncertain && <p className="mcp-warning" role="status">结果未知：操作可能已执行</p>}
      <p className="tool-provenance" title={ids}>{parts.join(" · ")}</p>
    </>
  );
}

/** Generic MCP body: result first, then arguments, then a quiet footer. */
export function McpToolBody({ item, toolCall, isError }: { item: Item; toolCall: ToolCall; isError: boolean }) {
  const result = parseObject(toolCall.resultJson);
  const data = record(result.data);
  const code = text(result.code);
  const serverText = text(data.text);
  const structured = data.structuredContent;
  const attachments = toolAttachments(result);
  const serverError = data.isError === true;
  return (
    <div className="mcp-body">
      {isError && code && code !== "ok" && <McpErrorNotice code={serverError ? "mcp_tool_error" : code} detail={serverText || undefined} />}
      {!isError && <ToolImages item={item} toolCall={toolCall} attachments={attachments} name="图像" />}
      {!isError && serverText && <ExpandableText value={serverText} lines={TEXT_PREVIEW_LINES} />}
      {structured !== undefined && (
        <details className="mcp-structured">
          <summary>结构化结果</summary>
          <pre className="mcp-text">{JSON.stringify(structured, null, 2)}</pre>
        </details>
      )}
      {toolCall.resultHash && toolCall.resultBytes !== undefined && (
        <ToolTextView key={toolCall.resultHash} sessionId={item.sessionId} toolCallId={toolCall.id} field="result"
          sha256={toolCall.resultHash} totalBytes={toolCall.resultBytes} />
      )}
      <McpArguments toolCall={toolCall} />
      <McpFooter toolCall={toolCall} result={result} />
    </div>
  );
}

interface BrowserView {
  page?: { url?: string | undefined; title?: string | undefined };
  tabs?: Array<{ index: number; current: boolean; title: string; url: string }>;
  result?: string;
  code?: string;
  snapshotLines?: number;
  console?: { errors: number; warnings: number };
  error?: string;
}

export function browserView(result: Record<string, unknown>): BrowserView {
  const view = record(record(result.data).browser);
  const page = record(view.page);
  const consoleCounts = record(view.console);
  return {
    ...(view.page !== undefined ? { page: { url: text(page.url) || undefined, title: text(page.title) || undefined } } : {}),
    ...(Array.isArray(view.tabs) ? {
      tabs: view.tabs.map(record).map((tab) => ({
        index: typeof tab.index === "number" ? tab.index : 0, current: tab.current === true, title: text(tab.title), url: text(tab.url),
      })),
    } : {}),
    ...(text(view.result) ? { result: text(view.result) } : {}),
    ...(text(view.code) ? { code: text(view.code) } : {}),
    ...(typeof view.snapshotLines === "number" ? { snapshotLines: view.snapshotLines } : {}),
    ...(typeof consoleCounts.errors === "number" ? { console: { errors: consoleCounts.errors, warnings: Number(consoleCounts.warnings) || 0 } } : {}),
    ...(text(view.error) ? { error: text(view.error) } : {}),
  };
}

/** Host of the page a browser tool ended on, from the Runtime projection or the raw text. */
export function browserPageHost(result: Record<string, unknown>): string {
  const url = browserView(result).page?.url || /Page URL: (\S+)/.exec(text(record(result.data).text))?.[1];
  if (!url) return "";
  try { return new URL(url).host || ""; } catch { return ""; }
}

function PageCard({ page }: { page: NonNullable<BrowserView["page"]> }) {
  if (!page.url && !page.title) return null;
  return (
    <div className="browser-page-card" title={page.url}>
      <span className="browser-page-card__title">{page.title || page.url}</span>
      {page.url && page.title && <span className="browser-page-card__url">{page.url}</span>}
    </div>
  );
}

function masked(field: string, value: string): string {
  return SECRET_FIELD.test(field) ? "••••" : value;
}

function browserAction(tool: string, args: Record<string, unknown>): string {
  const element = text(args.element);
  switch (tool) {
    case "browser_click": return element ? `点击「${element}」` : "";
    case "browser_hover": return element ? `悬停在「${element}」` : "";
    case "browser_select_option": {
      const values = Array.isArray(args.values) ? args.values.filter((value): value is string => typeof value === "string") : [];
      return element ? `在「${element}」选择 ${values.join("、")}` : "";
    }
    case "browser_type": {
      const typed = masked(element, text(args.text));
      return element ? `在「${element}」输入「${typed}」${args.submit === true ? "并提交" : ""}` : "";
    }
    case "browser_press_key": return text(args.key) ? `按下 ${text(args.key)}` : "";
    case "browser_drag": return `从「${text(args.startElement)}」拖到「${text(args.endElement)}」`;
    case "browser_wait_for":
      if (typeof args.time === "number") return `等待 ${args.time} 秒`;
      if (text(args.text)) return `等待「${text(args.text)}」出现`;
      if (text(args.textGone)) return `等待「${text(args.textGone)}」消失`;
      return "";
    case "browser_file_upload": {
      const paths = Array.isArray(args.paths) ? args.paths.filter((value): value is string => typeof value === "string") : [];
      return paths.length ? `上传 ${paths.map((path) => path.split("/").at(-1)).join("、")}` : "取消选择文件";
    }
    case "browser_handle_dialog": return args.accept === true ? "接受对话框" : "关闭对话框";
    case "browser_resize": return typeof args.width === "number" ? `调整窗口为 ${args.width}×${String(args.height)}` : "";
    default: return "";
  }
}

function FormFields({ args }: { args: Record<string, unknown> }) {
  const fields = Array.isArray(args.fields) ? args.fields.map(record) : [];
  if (fields.length === 0) return null;
  return (
    <dl className="mcp-arguments">
      {fields.slice(0, 12).map((field, index) => {
        const name = text(field.name) || `字段 ${index + 1}`;
        return <div key={`${name}-${index}`}><dt>{name}</dt><dd>{masked(name, argumentValue(field.value))}</dd></div>;
      })}
    </dl>
  );
}

/** Browser tools: what happened, on which page, with raw Playwright output tucked away. */
export function BrowserToolBody({ item, toolCall, tool, isError }: {
  item: Item; toolCall: ToolCall; tool: string; isError: boolean;
}) {
  const result = parseObject(toolCall.resultJson);
  const args = parseObject(toolCall.argumentsJson);
  const data = record(result.data);
  const view = browserView(result);
  const attachments = toolAttachments(result);
  const action = browserAction(tool, args);
  const code = text(result.code);
  const showResult = ["browser_find", "browser_console_messages", "browser_network_requests", "browser_network_request", "browser_tabs"].includes(tool);
  const rawText = text(data.text);
  return (
    <div className="mcp-body">
      {isError && <McpErrorNotice code={data.isError === true ? "mcp_tool_error" : code || "mcp_tool_error"} detail={view.error || rawText || undefined} />}
      {action && <p className="browser-action">{action}</p>}
      {tool === "browser_fill_form" && <FormFields args={args} />}
      {!isError && <ToolImages item={item} toolCall={toolCall} attachments={attachments} name="截图" />}
      {!isError && view.snapshotLines !== undefined && <p>页面结构 {view.snapshotLines} 行</p>}
      {!isError && showResult && tool !== "browser_tabs" && view.result && <ExpandableText value={view.result} lines={10} />}
      {!isError && tool === "browser_tabs" && view.tabs && (
        <ul className="browser-tabs">
          {view.tabs.map((tab) => (
            <li key={tab.index} className={tab.current ? "browser-tabs__current" : undefined} title={tab.url}>
              {tab.title || tab.url || "空白页"}{tab.current ? "（当前）" : ""}
            </li>
          ))}
        </ul>
      )}
      {view.page && <PageCard page={view.page} />}
      {view.console && view.console.errors > 0 && <p className="mcp-warning">控制台有 {view.console.errors} 个错误</p>}
      {(view.code || rawText) && (
        <details className="mcp-structured">
          <summary>技术详情</summary>
          {view.code && <pre className="mcp-text">{view.code}</pre>}
          {rawText && <pre className="mcp-text">{rawText}</pre>}
          {toolCall.resultHash && toolCall.resultBytes !== undefined && (
            <ToolTextView key={toolCall.resultHash} sessionId={item.sessionId} toolCallId={toolCall.id} field="result"
              sha256={toolCall.resultHash} totalBytes={toolCall.resultBytes} />
          )}
        </details>
      )}
      <McpFooter toolCall={toolCall} result={result} />
    </div>
  );
}
