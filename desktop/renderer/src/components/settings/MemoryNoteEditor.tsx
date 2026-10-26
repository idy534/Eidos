import { useEffect, useRef, useState } from "react";
import type { MemoryReadResult } from "../../../../shared/memory.generated.js";
import { Button } from "../Button.js";
import { ConfirmDialog } from "../ConfirmDialog.js";
import { MarkdownContent } from "../MarkdownContent.js";
import { StatusBadge } from "../StatusBadge.js";
import { EditPencilIcon, ClockHistoryIcon, ChevronDownIcon } from "./MemoryIcons.js";
import type { MemoryScopeChoice } from "./useMemoryDocuments.js";

export function MemoryNoteEditor({
  sessionId,
  scope,
  path,
  generation,
  onSaved,
  onSelect,
  onDirty,
  onOpenSource,
  onShowToast,
}: {
  sessionId: string | undefined;
  scope: MemoryScopeChoice;
  path: string | null;
  generation: number;
  onSaved: (path: string) => void;
  onSelect: (path: string) => void;
  onDirty: (dirty: boolean) => void;
  onOpenSource?: ((id: string) => void) | undefined;
  onShowToast?: ((message: string, type: "success" | "info" | "error") => void) | undefined;
}) {
  const [read, setRead] = useState<MemoryReadResult>();
  const [draft, setDraft] = useState({ path: "", summary: "", content: "" });
  const [editing, setEditing] = useState(path === null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(path !== null);
  const [deleting, setDeleting] = useState(false);
  const [revision, setRevision] = useState<number | undefined>();
  const [reload, setReload] = useState(0);
  const mounted = useRef(true);

  const dirty =
    editing &&
    (draft.content !== (read?.content ?? "") ||
      draft.summary !== (read?.document?.summary ?? "") ||
      (path === null && draft.path !== ""));
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;

  useEffect(() => {
    onDirty(dirty || busy);
  }, [dirty, busy, onDirty]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (path === null || dirtyRef.current) return;
    let active = true;
    setLoading(true);
    setRead(undefined);

    async function load() {
      let result: MemoryReadResult | undefined;
      let content = "";
      let offset = 0;
      for (let page = 0; page < 10; page++) {
        const value = await window.eidosRuntime.memory("memory/read", {
          sessionId: sessionId ?? null,
          scope,
          path: path!,
          offset,
          limit: 16384,
          revision: result?.revision ?? revision ?? null,
        });
        result ??= value;
        content += value.content;
        if (value.nextOffset == null) return { ...result, content };
        if (value.nextOffset <= offset) throw new Error("记忆分页无效。");
        offset = value.nextOffset;
      }
      throw new Error("笔记过长，请通过分页工具读取。");
    }

    void load()
      .then((value) => {
        if (!active) return;
        setRead(value);
        setDraft({
          path: value.path,
          summary: value.document?.summary ?? "",
          content: value.content,
        });
      })
      .catch(() => {
        if (active) {
          onShowToast?.("笔记无法读取，可能已更新或删除。请重新读取。", "error");
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [sessionId, scope, path, generation, revision, reload, onShowToast]);

  async function save(action: "write" | "delete") {
    if (busy) return;
    setBusy(true);
    try {
      await window.eidosRuntime.memory("memory/edit", {
        sessionId: sessionId ?? null,
        scope,
        operationId: crypto.randomUUID(),
        action,
        path: path ?? draft.path,
        expectedRevision: read?.revision ?? 0,
        ...(action === "write"
          ? {
              summary: draft.summary,
              content: draft.content,
              title: read?.document?.title ?? draft.path.replace(/\.md$/, ""),
              kind: read?.document?.kind ?? "feedback",
            }
          : {}),
      });
      if (!mounted.current) return;
      setEditing(false);
      setDeleting(false);
      setRevision(undefined);
      dirtyRef.current = false;
      onDirty(false);
      onSaved(action === "delete" ? "MEMORY.md" : path ?? draft.path);
      setReload((value) => value + 1);
    } catch {
      if (mounted.current) {
        onShowToast?.(
          "保存失败，笔记可能已被其他任务修改。你的编辑仍在此处，请重新核对后保存。",
          "error",
        );
      }
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  const index = path === "MEMORY.md";
  const current = !read || read.revision === read.currentRevision;

  return (
    <section
      className="memory-note"
      aria-label="记忆正文"
      aria-busy={loading || busy}
    >
      <header className="memory-note-heading">
        <div className="memory-note-title-wrap">
          <div className="memory-note-tag-row">
            <h2>{path ?? "新建笔记"}</h2>
            {index ? (
              <StatusBadge tone="neutral" className="memory-tag-badge">
                系统索引
              </StatusBadge>
            ) : read?.revision != null && !editing ? (
              <span className="memory-revision-badge">
                v{read.revision}
                {current ? "（最新）" : "（历史）"}
              </span>
            ) : null}
          </div>
          <p>
            {index
              ? "模型先读取这份索引，再按需打开笔记。"
              : read?.document?.archived
              ? "这份笔记已归档，后续对话不会自动使用。你可以恢复它或彻底删除。"
              : "笔记会保留适用条件，并在后续对话中使用。"}
          </p>
        </div>

        {!index && !editing && read && current && (
          <Button
            size="small"
            variant={read.document?.archived ? "primary" : "secondary"}
            icon={read.document?.archived ? undefined : <EditPencilIcon />}
            disabled={loading || busy}
            onClick={() => read.document?.archived ? void save("write") : setEditing(true)}
          >
            {read.document?.archived ? (busy ? "正在恢复…" : "恢复笔记") : "编辑"}
          </Button>
        )}
      </header>

      {loading ? (
        <div className="memory-note-loading" role="status">
          <div className="memory-spinner" />
          <p>正在读取笔记…</p>
        </div>
      ) : editing ? (
        <form
          className="memory-note-form"
          onSubmit={(event) => {
            event.preventDefault();
            void save("write");
          }}
        >
          {path === null && (
            <label className="memory-form-item">
              <span className="memory-form-label">文件名</span>
              <input
                aria-label="笔记文件名"
                placeholder="feedback_testing.md"
                maxLength={200}
                value={draft.path}
                disabled={busy}
                onChange={(event) =>
                  setDraft({ ...draft, path: event.target.value })
                }
              />
            </label>
          )}

          <label className="memory-form-item">
            <span className="memory-form-label">简短摘要</span>
            <input
              aria-label="笔记摘要"
              placeholder="可选，留空时会自动补充摘要…"
              maxLength={240}
              value={draft.summary}
              disabled={busy}
              onChange={(event) =>
                setDraft({ ...draft, summary: event.target.value })
              }
            />
          </label>

          <label className="memory-form-item memory-form-item--grow">
            <span className="memory-form-label">Markdown 正文</span>
            <textarea
              aria-label="笔记正文"
              rows={16}
              maxLength={65536}
              placeholder="输入正文内容，支持标准 Markdown…"
              value={draft.content}
              disabled={busy}
              onChange={(event) =>
                setDraft({ ...draft, content: event.target.value })
              }
            />
          </label>

          <div className="memory-note-actions">
            <span className="memory-dirty-indicator">
              {dirty ? "尚未保存" : ""}
            </span>
            <Button
              type="button"
              variant="ghost"
              disabled={busy}
              onClick={() => {
                dirtyRef.current = false;
                setEditing(false);
                onDirty(false);
                if (path === null) onSelect("MEMORY.md");
                else setReload((value) => value + 1);
              }}
            >
              取消编辑
            </Button>
            <Button
              type="submit"
              variant="primary"
              disabled={
                busy ||
                !draft.content.trim() ||
                (path === null && !draft.path.endsWith(".md"))
              }
            >
              {busy ? "正在保存…" : "保存笔记"}
            </Button>
          </div>
        </form>
      ) : (
        <div className="memory-note-body">
          <MarkdownContent
            content={read?.content ?? ""}
            onNoteLink={onSelect}
          />
        </div>
      )}

      {read?.document && !editing && (
        <details className="memory-note-details">
          <summary className="memory-details-summary">
            <div className="memory-details-summary-title">
              <ClockHistoryIcon className="memory-summary-icon" />
              <span>来源和版本</span>
            </div>
            <ChevronDownIcon className="memory-summary-chevron" />
          </summary>

          <div className="memory-details-body">
            <div className="memory-meta-row">
              <p className="memory-meta-updated">
                更新时间：{new Date(read.document.updatedAt).toLocaleString()}
              </p>
              <label className="memory-meta-version-select">
                <span>版本</span>
                <select
                  aria-label="记忆版本"
                  value={read.revision}
                  onChange={(event) => setRevision(Number(event.target.value))}
                >
                  {(read.revisions ?? []).map((value) => (
                    <option key={value} value={value}>
                      v{value}
                      {value === read.currentRevision ? "（当前）" : ""}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <div className="memory-details-actions">
              <div className="memory-evidence-group">
                {(read.document.evidence ?? []).map((source) => (
                  <Button
                    key={source.itemId}
                    variant="secondary"
                    size="small"
                    disabled={!onOpenSource}
                    onClick={() => onOpenSource?.(source.sessionId)}
                  >
                    查看来源对话
                  </Button>
                ))}
              </div>

              {current && (
                <Button
                  variant="danger"
                  size="small"
                  onClick={() => setDeleting(true)}
                >
                  删除笔记
                </Button>
              )}
            </div>
          </div>
        </details>
      )}

      <ConfirmDialog
        open={deleting}
        title="删除这份记忆"
        description="正文和历史版本将被删除，相关上下文将被撤销。原始聊天会保留。"
        confirmLabel="删除笔记"
        isDestructive
        busy={busy}
        onConfirm={() => void save("delete")}
        onCancel={() => setDeleting(false)}
      />
    </section>
  );
}
