import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Diff, Hunk, getChangeKey, parseDiff } from "react-diff-view";
import type { Item } from "../contracts.js";
import { useArtifacts } from "./ArtifactContext.js";
import { userFacingError } from "../session-state.js";
import { ToolTextView } from "./ToolTextView.js";
import { Button } from "./Button.js";
import { WorkspaceFolderIcon } from "./WorkspaceFileIcon.js";

function cleanFilePath(file: { oldPath: string; newPath: string }): string {
  const raw = file.newPath && file.newPath !== "/dev/null" ? file.newPath : file.oldPath;
  return raw.replace(/^[ab]\//, "");
}

function cleanFocusPath(focus?: string): string | undefined {
  if (!focus) return undefined;
  return focus.replace(/^[ab]\//, "");
}

function matchesFocus(file: { oldPath: string; newPath: string }, focus?: string): boolean {
  if (!focus) return true;
  const clean = cleanFilePath(file);
  const cleanFocus = cleanFocusPath(focus) ?? focus;
  return clean === cleanFocus || file.oldPath === focus || file.newPath === focus;
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

function OpenInEditorIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M8 4H5.5A1.5 1.5 0 0 0 4 5.5V14A1.5 1.5 0 0 0 5.5 15.5H14.5A1.5 1.5 0 0 0 16 14V11.5M11.5 4H16V8.5M16 4 10 10" />
    </svg>
  );
}

export interface ItemReviewStats {
  additions: number;
  deletions: number;
  count: number;
  allExpanded: boolean;
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
  openPath?: string;
}

export function LastTurnChanges({ sessionId, previousItemId, items, runId, focusPath, onFeedback, disabled, entireTask = false, onStats, expandSignal }: {
  entireTask?: boolean;
  sessionId: string;
  previousItemId?: string | undefined;
  items: Item[];
  runId?: string | undefined;
  focusPath?: string | undefined;
  onFeedback?: ((text: string) => Promise<void>) | undefined;
  disabled: boolean;
  onStats?: ((stats: ItemReviewStats) => void) | undefined;
  expandSignal?: { id: number; expand: boolean } | undefined;
}) {
  const actions = useArtifacts();
  const [older, setOlder] = useState<Item[]>([]);
  const [cursor, setCursor] = useState(previousItemId);
  const [loading, setLoading] = useState(false);
  const currentItems = useMemo(() => [...new Map([...older, ...items].map((item) => [item.id, item])).values()]
    .filter((item) => entireTask || item.runId === runId)
    .sort((a, b) => a.createdAt - b.createdAt || a.ordinal - b.ordinal),
  [older, items, entireTask, runId]);

  const fileSummaries = useMemo<FileReviewSummary[]>(() => {
    const map = new Map<string, FileReviewSummary>();
    for (const item of currentItems) {
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
      const fallbackPath = fallbackItemPath(item);
      try {
        const parsed = parseDiff(call.changeDiff).filter((f) => {
          if (matchesFocus(f, focusPath)) return true;
          if (!cleanFilePath(f) && fallbackPath && focusPath) {
            return fallbackPath === (cleanFocusPath(focusPath) ?? focusPath);
          }
          return false;
        });
        for (const file of parsed) {
          const cleanPath = cleanFilePath(file) || fallbackPath;
          if (!cleanPath) continue;
          const stats = computeDiffStats(file.hunks);
          const existing = map.get(cleanPath) ?? {
            path: cleanPath,
            additions: 0,
            deletions: 0,
            changes: [],
            openPath: cleanPath,
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
  }, [currentItems, focusPath]);

  const totalStats = useMemo(() => {
    let additions = 0;
    let deletions = 0;
    for (const file of fileSummaries) {
      additions += file.additions;
      deletions += file.deletions;
    }
    return { additions, deletions };
  }, [fileSummaries]);

  const focused = cleanFocusPath(focusPath);
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(() => (
    focused ? new Set([focused]) : new Set()
  ));

  useEffect(() => {
    if (focused) {
      setExpandedKeys((prev) => new Set([...prev, focused]));
    }
  }, [focused]);

  useEffect(() => {
    if (fileSummaries.length === 1 && expandedKeys.size === 0) {
      const first = fileSummaries[0];
      if (first) setExpandedKeys(new Set([first.path]));
    }
  }, [fileSummaries, expandedKeys.size]);

  const allFilesExpanded = fileSummaries.length > 0
    && fileSummaries.every((f) => expandedKeys.has(f.path));

  useEffect(() => {
    onStats?.({
      additions: totalStats.additions,
      deletions: totalStats.deletions,
      count: fileSummaries.length,
      allExpanded: allFilesExpanded,
    });
  }, [onStats, totalStats.additions, totalStats.deletions, fileSummaries.length, allFilesExpanded]);

  useEffect(() => {
    if (!expandSignal) return;
    if (expandSignal.expand) {
      setExpandedKeys(new Set(fileSummaries.map((f) => f.path)));
    } else {
      setExpandedKeys(new Set());
    }
    // fileSummaries 变化时沿用最近一次展开意图，保持工具栏与列表一致。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expandSignal]);

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

  const changesCount = currentItems.filter((item) => item.toolCall?.changeDiff || item.toolCall?.changeDiffHash).length;
  const hasFocusedChange = !focusPath || fileSummaries.some((f) => f.openPath !== undefined)
    || changesCount > 0 && currentItems.some((item) => item.toolCall?.changeDiffHash);
  // 行内草稿与未提交共用同一交互：点 gutter 在该行下方展开，其余位置不变。
  const [draft, setDraft] = useState<{
    filePath: string;
    toolCallId: string;
    changeKey: string;
    anchorText: string;
  }>();
  const [draftBody, setDraftBody] = useState("");
  const [sendError, setSendError] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function loadOlder() {
    if (!cursor) return;
    setLoading(true); setError("");
    try {
      const page = await window.eidosRuntime.readSession(sessionId, { beforeItemId: cursor, itemLimit: 200 });
      setOlder((current) => [...page.items, ...current]);
      if (entireTask) {
        setCursor(page.previousItemId);
      } else {
        setCursor(page.items.some((item) => item.runId !== runId) ? undefined : page.previousItemId);
      }
    } catch (cause) { setError(userFacingError(cause)); }
    finally { setLoading(false); }
  }

  async function send() {
    if (!onFeedback || !draft) return;
    setBusy(true); setSendError("");
    try {
      await onFeedback(`请处理以下文本修改反馈。以下位置属于工具执行时的 Diff，请先核对当前文件。\n${draft.anchorText}\n用户意见：${draftBody}`);
      setDraftBody("");
      setDraft(undefined);
    } catch (cause) { setSendError(userFacingError(cause)); }
    finally { setBusy(false); }
  }

  function draftWidgets(filePath: string, toolCallId: string): Record<string, ReactNode> {
    if (!draft || draft.filePath !== filePath || draft.toolCallId !== toolCallId) return {};
    return {
      [draft.changeKey]: (
        <div className="review-comment-draft">
          <textarea
            aria-label="本轮修改反馈"
            value={draftBody}
            onChange={(event) => setDraftBody(event.target.value)}
            placeholder="输入针对选中代码行的修改意见..."
            maxLength={8192}
          />
          <div>
            <Button
              variant="secondary"
              size="small"
              disabled={disabled || busy || !draftBody.trim()}
              loading={busy}
              onClick={() => void send()}
            >
              发送反馈
            </Button>
            <Button
              variant="ghost"
              size="small"
              onClick={() => { setDraft(undefined); setDraftBody(""); setSendError(""); }}
            >
              取消
            </Button>
          </div>
          {sendError && <p className="text-review-error" role="alert">{sendError}</p>}
        </div>
      ),
    };
  }

  return (
    <section className="last-turn-changes" aria-label={entireTask ? "整个任务修改记录" : "最近一轮修改"}>
      {cursor && (
        <div className="text-review-load-older">
          <Button variant="secondary" size="small" disabled={loading} onClick={() => void loadOlder()}>
            加载更早的修改记录
          </Button>
        </div>
      )}
      {error && <p className="text-review-error" role="alert">{error}</p>}
      <div className="git-review-files">
        {fileSummaries.length > 0 && (
          <section className="git-file-group" aria-label="修改">
            <h2>修改 <span>{fileSummaries.length}</span></h2>
            {fileSummaries.map((file) => {
          const isFileExpanded = expandedKeys.has(file.path);
          return (
            <article className="git-review-file" key={file.path}>
              <header className="git-review-file-header">
                <button
                  type="button"
                  className="git-file-button"
                  aria-expanded={isFileExpanded}
                  aria-controls={`last-turn-diff-${encodeURIComponent(file.path)}`}
                  aria-label={file.path}
                  onClick={() => toggleFile(file.path)}
                >
                  <span className="git-file-disclosure">
                    <FileDisclosureIcon expanded={isFileExpanded} />
                  </span>
                  <code>{file.path}</code>
                  <span className="git-file-stats">
                    <span className="git-review-stat--addition">+{file.additions}</span>
                    <span className="git-review-stat--deletion">-{file.deletions}</span>
                  </span>
                </button>
                {file.openPath && (
                  <>
                    <Button
                      size="small"
                      variant="ghost"
                      className="git-icon-button"
                      icon={<OpenInEditorIcon />}
                      aria-label={`在编辑器中打开 ${file.openPath}`}
                      title={`在编辑器中打开 ${file.openPath}`}
                      onClick={() => {
                        setError("");
                        void window.eidosRuntime.openWorkspacePathInEditor(sessionId, file.openPath!).catch((cause: unknown) => {
                          setError(userFacingError(cause));
                        });
                      }}
                    >
                      <span className="sr-only">{`在编辑器中打开 ${file.openPath}`}</span>
                    </Button>
                    <Button
                      size="small"
                      variant="ghost"
                      className="git-icon-button"
                      icon={<WorkspaceFolderIcon />}
                      aria-label={`在工作区打开 ${file.openPath}`}
                      title={`在工作区打开 ${file.openPath}`}
                      onClick={() => actions?.openFile(file.openPath!)}
                    >
                      <span className="sr-only">{`在工作区打开 ${file.openPath}`}</span>
                    </Button>
                  </>
                )}
              </header>

              {isFileExpanded && (
                <div
                  id={`last-turn-diff-${encodeURIComponent(file.path)}`}
                  className="git-file-diff-scroll"
                >
                  {focusPath && !file.openPath && (
                    <p className="text-review-note">完整补丁包含此次工具调用的全部文件。</p>
                  )}
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
                              setDraft({
                                filePath: file.path,
                                toolCallId: change.toolCallId,
                                changeKey: getChangeKey(lineChange),
                                anchorText: `Run: ${change.item.runId}\nToolCall: ${call.id}\n文件: ${file.openPath ?? file.path}\n位置: ${side ?? "new"} 第 ${line} 行\nBase SHA: ${call.baseSha256 ?? "无"}`,
                              });
                              setDraftBody("");
                              setSendError("");
                            },
                          }}
                          widgets={draftWidgets(file.path, change.toolCallId)}
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
          </section>
        )}
      </div>
      {!fileSummaries.length && (
        <div className="text-review-empty">
          <p>{entireTask ? "整个任务尚无可展示的文本补丁。" : "本轮还没有已记录的文件补丁。"}</p>
        </div>
      )}
      {focusPath && fileSummaries.length > 0 && !hasFocusedChange && (
        <div className="text-review-empty">
          <p>未找到该文件的历史 Diff。</p>
        </div>
      )}
    </section>
  );
}
