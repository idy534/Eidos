import { useCallback, useEffect, useMemo, useState } from "react";
import { Diff, Hunk, parseDiff } from "react-diff-view";
import type { Item } from "../contracts.js";
import { userFacingError } from "../session-state.js";
import { useArtifacts } from "./ArtifactContext.js";
import { Button } from "./Button.js";
import { ToolTextView } from "./ToolTextView.js";
import { WorkspaceFileIcon } from "./WorkspaceFileIcon.js";

function cleanFilePath(file: { oldPath: string; newPath: string }): string {
  const raw = file.newPath && file.newPath !== "/dev/null" ? file.newPath : file.oldPath;
  return raw.replace(/^[ab]\//, "");
}

function fallbackItemPath(item: Item): string | undefined {
  const raw = item.toolCall?.resultJson;
  if (!raw) return undefined;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return undefined;
    const data = (parsed as Record<string, unknown>).data;
    if (!data || typeof data !== "object" || Array.isArray(data)) return undefined;
    const rec = data as Record<string, unknown>;
    const path = rec.path;
    if (typeof path === "string" && path && !path.includes("\0")) return path.replace(/^\.\//, "");
    const changes = rec.changes;
    if (Array.isArray(changes)) {
      for (const value of changes) {
        if (!value || typeof value !== "object") continue;
        const record = value as Record<string, unknown>;
        const candidate = record.newPath ?? record.path;
        if (typeof candidate === "string" && candidate && !candidate.includes("\0")) {
          return candidate.replace(/^\.\//, "");
        }
      }
    }
    for (const key of ["created", "modified", "deleted"]) {
      const list = rec[key];
      if (Array.isArray(list)) {
        const first = list.find((entry): entry is string => typeof entry === "string" && Boolean(entry));
        if (first) return first.replace(/^\.\//, "");
      }
    }
  } catch {
    return undefined;
  }
  return undefined;
}

function computeDiffStats(hunks: Array<{ changes: Array<{ type: string }> }>): { additions: number; deletions: number } {
  let additions = 0;
  let deletions = 0;
  for (const hunk of hunks) {
    for (const change of hunk.changes) {
      if (change.type === "insert") additions++;
      else if (change.type === "delete") deletions++;
    }
  }
  return { additions, deletions };
}

function ExpandIcon({ collapse }: { collapse: boolean }) {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      {collapse
        ? <path d="M5 3v5m-2-2 2 2 2-2M5 17v-5m-2 2 2-2 2 2M10 5h7M10 10h7M10 15h7" />
        : <path d="M5 8V3m-2 2 2-2 2 2M5 12v5m-2-2 2 2 2-2M10 5h7M10 10h7M10 15h7" />}
    </svg>
  );
}

function RefreshIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M16.5 8a6.5 6.5 0 0 0-11.1-2L4 7.5M3.5 4.5v3h3M3.5 12a6.5 6.5 0 0 0 11.1 2L16 12.5M16.5 15.5v-3h-3" />
    </svg>
  );
}

function FileDisclosureIcon({ expanded }: { expanded: boolean }) {
  return (
    <svg
      viewBox="0 0 20 20"
      className="git-file-disclosure-icon"
      data-state={expanded ? "open" : "closed"}
      aria-hidden="true"
    >
      <path d="m7 4 5 6-5 6" />
    </svg>
  );
}

interface FileChangeEntry {
  item: Item;
  toolCallId: string;
  parsedFile?: ReturnType<typeof parseDiff>[number];
  rawDiff?: string;
  hash?: string;
  bytes?: number;
}

interface FileReviewSummary {
  path: string;
  additions: number;
  deletions: number;
  changes: FileChangeEntry[];
}

export interface TextReviewPanelProps {
  sessionId: string;
  runId?: string | undefined;
  path?: string | undefined;
  items: Item[];
  loading?: boolean | undefined;
  error?: string | undefined;
  onFeedback?(text: string): Promise<void>;
  disabled?: boolean | undefined;
  onRefresh?: (() => void) | undefined;
  expanded?: boolean | undefined;
}

export function TextReviewPanel({
  sessionId,
  runId,
  path,
  items,
  loading = false,
  error,
  onFeedback,
  disabled = false,
  onRefresh,
  expanded = false,
}: TextReviewPanelProps) {
  const actions = useArtifacts();
  const [scope, setScope] = useState<"turn" | "task">("turn");
  const [anchor, setAnchor] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [feedbackError, setFeedbackError] = useState("");

  const scopedItems = useMemo(() => {
    if (scope === "turn") {
      return items.filter((item) => item.runId === runId);
    }
    return items;
  }, [items, scope, runId]);

  const fileSummaries = useMemo<FileReviewSummary[]>(() => {
    const map = new Map<string, FileReviewSummary>();
    const sorted = [...scopedItems].sort((a, b) => a.createdAt - b.createdAt || a.ordinal - b.ordinal);

    for (const item of sorted) {
      const call = item.toolCall;
      if (!call) continue;

      if (call.changeDiffHash) {
        const pathKey = `patch-${call.id}`;
        const existing = map.get(pathKey) ?? {
          path: `补丁 ${call.id.slice(0, 8)}`,
          additions: 0,
          deletions: 0,
          changes: [],
        };
        existing.changes.push({
          item,
          toolCallId: call.id,
          hash: call.changeDiffHash,
          bytes: call.changeDiffBytes ?? 0,
        });
        map.set(pathKey, existing);
        continue;
      }

      if (!call.changeDiff) continue;

      try {
        const parsed = parseDiff(call.changeDiff);
        for (const file of parsed) {
          const cleanPath = cleanFilePath(file) || fallbackItemPath(item);
          if (!cleanPath) continue;
          const stats = computeDiffStats(file.hunks);
          const existing = map.get(cleanPath) ?? {
            path: cleanPath,
            additions: 0,
            deletions: 0,
            changes: [],
          };
          existing.additions += stats.additions;
          existing.deletions += stats.deletions;
          existing.changes.push({
            item,
            toolCallId: call.id,
            parsedFile: file,
          });
          map.set(cleanPath, existing);
        }
      } catch {
        const pathKey = `raw-${call.id}`;
        const existing = map.get(pathKey) ?? {
          path: `未格式化变更 (${call.id.slice(0, 8)})`,
          additions: 0,
          deletions: 0,
          changes: [],
        };
        existing.changes.push({
          item,
          toolCallId: call.id,
          rawDiff: call.changeDiff,
        });
        map.set(pathKey, existing);
      }
    }

    return Array.from(map.values()).sort((a, b) => a.path.localeCompare(b.path));
  }, [scopedItems]);

  const totalStats = useMemo(() => {
    let additions = 0;
    let deletions = 0;
    for (const file of fileSummaries) {
      additions += file.additions;
      deletions += file.deletions;
    }
    return { additions, deletions };
  }, [fileSummaries]);

  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(() => {
    return path ? new Set([path]) : new Set();
  });

  useEffect(() => {
    if (path) {
      setExpandedKeys((prev) => new Set([...prev, path]));
    }
  }, [path]);

  useEffect(() => {
    const first = fileSummaries[0];
    if (fileSummaries.length === 1 && expandedKeys.size === 0 && first) {
      setExpandedKeys(new Set([first.path]));
    }
  }, [fileSummaries, expandedKeys.size]);

  const allFilesExpanded = fileSummaries.length > 0
    && fileSummaries.every((f) => expandedKeys.has(f.path));

  const toggleAllExpanded = useCallback(() => {
    if (allFilesExpanded) {
      setExpandedKeys(new Set());
    } else {
      setExpandedKeys(new Set(fileSummaries.map((f) => f.path)));
    }
  }, [allFilesExpanded, fileSummaries]);

  const toggleFile = useCallback((filePath: string) => {
    setExpandedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(filePath)) {
        next.delete(filePath);
      } else {
        next.add(filePath);
      }
      return next;
    });
  }, []);

  const sendFeedback = async () => {
    if (!onFeedback || !body.trim()) return;
    setBusy(true);
    setFeedbackError("");
    try {
      await onFeedback(`请处理以下文本修改反馈。以下位置属于工具执行时的 Diff，请先核对当前文件。\n${anchor}\n用户意见：${body}`);
      setBody("");
      setAnchor("");
    } catch (cause) {
      setFeedbackError(userFacingError(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section
      className={`git-changes-panel${expanded ? " git-changes-panel--expanded" : ""}`}
      aria-label="文本修改审查"
    >
      <header className="git-changes-toolbar" aria-label="审查工具栏">
        <div className="git-changes-toolbar-left">
          <div className="git-scope-tabs" role="tablist" aria-label="审查范围">
            <button
              type="button"
              role="tab"
              className="git-scope-tab"
              aria-selected={scope === "turn"}
              onClick={() => setScope("turn")}
            >
              最近一轮修改
            </button>
            <button
              type="button"
              role="tab"
              className="git-scope-tab"
              aria-selected={scope === "task"}
              onClick={() => setScope("task")}
            >
              整个任务修改
            </button>
          </div>

          <span className="text-review-files-count">
            共 {fileSummaries.length} 个文件修改
          </span>

          <div className="git-review-stats" aria-label="变更统计">
            <span className="git-review-stat git-review-stat--addition">+{totalStats.additions}</span>
            <span className="git-review-stat git-review-stat--deletion">-{totalStats.deletions}</span>
          </div>
        </div>

        <div className="git-changes-toolbar-right">
          <Button
            variant="ghost"
            size="small"
            className="git-icon-button"
            icon={<ExpandIcon collapse={allFilesExpanded} />}
            aria-label={allFilesExpanded ? "折叠全部差异" : "展开全部差异"}
            title={allFilesExpanded ? "折叠全部差异" : "展开全部差异"}
            disabled={fileSummaries.length === 0}
            onClick={toggleAllExpanded}
          >
            <span className="sr-only">{allFilesExpanded ? "折叠全部差异" : "展开全部差异"}</span>
          </Button>
          <Button
            variant="ghost"
            size="small"
            className="git-icon-button"
            icon={<RefreshIcon />}
            loading={loading}
            aria-label="刷新修改记录"
            title="刷新修改记录"
            onClick={onRefresh}
          >
            <span className="sr-only">刷新修改记录</span>
          </Button>
        </div>
      </header>

      {error && (
        <p className="approval-error git-review-error" role="alert">
          修改记录尚未完整读取：{error}
        </p>
      )}

      <div className="git-review-files">
        {loading && fileSummaries.length === 0 && (
          <p className="text-review-status" role="status">正在读取更早的修改记录…</p>
        )}

        {fileSummaries.length === 0 && !loading && !error && (
          <div className="git-review-empty">
            <strong>{scope === "turn" ? "本轮还没有已记录的文件补丁。" : "整个任务尚无可展示的文本补丁。"}</strong>
            <span>当智能体编辑工作区文件时，修改差异将显示在此处。</span>
          </div>
        )}

        {fileSummaries.map((file) => {
          const isFileExpanded = expandedKeys.has(file.path);
          return (
            <article className="git-review-file" key={file.path}>
              <header className="git-review-file-header">
                <button
                  type="button"
                  className="git-file-button"
                  aria-expanded={isFileExpanded}
                  aria-controls={`text-review-diff-${encodeURIComponent(file.path)}`}
                  aria-label={file.path}
                  onClick={() => toggleFile(file.path)}
                >
                  <span className="git-file-disclosure">
                    <FileDisclosureIcon expanded={isFileExpanded} />
                  </span>
                  <span className="text-review-file-icon" aria-hidden="true">
                    <WorkspaceFileIcon name={file.path} />
                  </span>
                  <code>{file.path}</code>
                  <span className="git-file-stats">
                    <span className="git-review-stat--addition">+{file.additions}</span>
                    <span className="git-review-stat--deletion">-{file.deletions}</span>
                  </span>
                </button>
                {isFileExpanded && (
                  <div className="git-file-actions">
                    <Button
                      size="small"
                      variant="ghost"
                      onClick={() => actions?.openFile(file.path)}
                      title={`在工作区打开 ${file.path}`}
                    >
                      在工作区打开
                    </Button>
                  </div>
                )}
              </header>

              {isFileExpanded && (
                <div
                  id={`text-review-diff-${encodeURIComponent(file.path)}`}
                  className="git-file-diff-scroll"
                >
                  {file.changes.map((change, idx) => {
                    if (change.hash) {
                      return (
                        <ToolTextView
                          key={change.hash}
                          sessionId={sessionId}
                          toolCallId={change.toolCallId}
                          field="diff"
                          sha256={change.hash}
                          totalBytes={change.bytes!}
                        />
                      );
                    }
                    if (change.parsedFile) {
                      return (
                        <Diff
                          key={`${change.item.id}:${idx}`}
                          className="git-diff-unified"
                          viewType="unified"
                          diffType={change.parsedFile.type}
                          hunks={change.parsedFile.hunks}
                          gutterClassName="git-diff-line-numbers"
                          renderGutter={({ change: lineChange, side, renderDefault }) => {
                            if (side === "new") return null;
                            return lineChange.type === "insert" ? lineChange.lineNumber : renderDefault();
                          }}
                          gutterEvents={{
                            onClick: ({ change: lineChange, side }) => {
                              if (!lineChange) return;
                              const line = lineChange.type === "normal"
                                ? (side === "old" ? lineChange.oldLineNumber : lineChange.newLineNumber)
                                : lineChange.lineNumber;
                              const call = change.item.toolCall!;
                              setAnchor(`Run: ${change.item.runId}\nToolCall: ${call.id}\n文件: ${file.path}\n位置: ${side ?? "new"} 第 ${line} 行\nBase SHA: ${call.baseSha256 ?? "无"}`);
                            },
                          }}
                        >
                          {(hunks) => hunks.map((hunk) => <Hunk key={hunk.content} hunk={hunk} />)}
                        </Diff>
                      );
                    }
                    if (change.rawDiff) {
                      return (
                        <pre key={change.item.id} className="text-review-raw-diff">
                          {change.rawDiff}
                        </pre>
                      );
                    }
                    return null;
                  })}
                </div>
              )}
            </article>
          );
        })}

        {anchor && (
          <div className="text-review-feedback-box" role="region" aria-label="代码审阅反馈">
            <div className="text-review-feedback-header">
              <span className="text-review-feedback-title">针对所选代码行的修改意见</span>
              <button
                type="button"
                className="text-review-feedback-close"
                aria-label="取消反馈"
                title="取消反馈"
                onClick={() => { setAnchor(""); setBody(""); }}
              >
                ×
              </button>
            </div>
            <pre className="text-review-feedback-anchor">{anchor}</pre>
            <textarea
              className="text-review-feedback-input"
              aria-label="本轮修改反馈"
              placeholder="输入针对选中代码行的修改意见..."
              value={body}
              maxLength={8192}
              onChange={(event) => setBody(event.target.value)}
            />
            <div className="text-review-feedback-actions">
              <Button
                variant="ghost"
                size="small"
                onClick={() => { setAnchor(""); setBody(""); }}
              >
                取消
              </Button>
              <Button
                variant="secondary"
                size="small"
                disabled={disabled || busy || !body.trim()}
                loading={busy}
                onClick={() => void sendFeedback()}
              >
                发送反馈
              </Button>
            </div>
            {feedbackError && <p className="text-review-error" role="alert">{feedbackError}</p>}
          </div>
        )}
      </div>
    </section>
  );
}
