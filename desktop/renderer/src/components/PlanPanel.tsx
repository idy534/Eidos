import { useRef, useState } from "react";
import type { PlanDocument } from "../../../shared/planning.generated.js";
import { userFacingError } from "../session-state.js";
import { MarkdownContent } from "./MarkdownContent.js";
import { Button } from "./Button.js";
import "./planning.css";

export interface PlanPanelProps {
  sessionId: string;
  plan: PlanDocument | undefined;
  historyPlans?: PlanDocument[] | undefined;
  ready: boolean;
  canEdit: boolean;
  onExecute: (plan: PlanDocument) => Promise<boolean>;
  onRevise: (plan: PlanDocument, feedback: string) => Promise<boolean>;
  onSaved?: (() => void) | undefined;
}

export function PlanPanel({
  sessionId: _sessionId,
  plan,
  historyPlans = [],
  ready,
  canEdit,
  onExecute,
  onRevise,
  onSaved,
}: PlanPanelProps) {
  const [editing, setEditing] = useState(false);
  const [markdown, setMarkdown] = useState(plan?.markdown ?? "");
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const lock = useRef(false);

  // Sync markdown if plan changes
  if (plan && !editing && markdown !== plan.markdown) {
    setMarkdown(plan.markdown);
  }

  if (!plan) {
    return (
      <div className="plan-dock-panel plan-dock-panel--empty" role="region" aria-label="计划">
        <p className="plan-dock-empty-text">当前会话暂无计划。</p>
      </div>
    );
  }

  const perform = async (action: () => Promise<unknown>) => {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      await action();
      onSaved?.();
    } catch (cause) {
      setError(userFacingError(cause));
    } finally {
      lock.current = false;
      setBusy(false);
    }
  };

  const disabled = busy || !ready || !canEdit || plan.status === "accepted";

  const handleSave = () => {
    void perform(async () => {
      if (typeof window.eidosRuntime?.editPlan !== "function") {
        throw new Error("当前环境不支持编辑计划");
      }
      await window.eidosRuntime.editPlan({
        planId: plan.id,
        expectedRevision: plan.revision,
        markdown,
      });
      setEditing(false);
    });
  };

  const handleExecute = () => {
    void perform(async () => {
      await onExecute(plan);
    });
  };

  const handleRevise = () => {
    if (!feedback.trim()) return;
    void perform(async () => {
      await onRevise(plan, feedback.trim());
      setFeedback("");
    });
  };

  return (
    <div className="plan-dock-panel" role="region" aria-label="计划面板">
      <header className="plan-dock-header">
        <div className="plan-dock-title-group">
          <h2 className="plan-dock-title">{plan.title}</h2>
          <span className="plan-dock-badge">
            版本 {plan.revision} ·{" "}
            {plan.status === "review"
              ? "待确认"
              : plan.status === "accepted"
                ? "已确认执行"
                : "草稿"}
          </span>
        </div>

        <div className="plan-dock-header-actions">
          {canEdit && !editing && plan.status !== "accepted" && (
            <Button
              type="button"
              variant="secondary"
              size="small"
              disabled={disabled}
              onClick={() => setEditing(true)}
            >
              编辑
            </Button>
          )}
          {editing && (
            <>
              <Button
                type="button"
                variant="ghost"
                size="small"
                disabled={busy}
                onClick={() => {
                  setMarkdown(plan.markdown);
                  setEditing(false);
                }}
              >
                取消
              </Button>
              <Button
                type="button"
                variant="primary"
                size="small"
                loading={busy}
                disabled={busy || !markdown.trim()}
                onClick={handleSave}
              >
                保存修改
              </Button>
            </>
          )}
        </div>
      </header>

      {error && (
        <p className="plan-dock-error" role="alert">
          {error}
        </p>
      )}

      <div className="plan-dock-content">
        {editing ? (
          <textarea
            className="plan-dock-editor"
            value={markdown}
            disabled={busy}
            onChange={(e) => setMarkdown(e.target.value)}
            placeholder="输入计划的 Markdown 内容…"
          />
        ) : (
          <div className="plan-dock-markdown">
            <MarkdownContent content={plan.markdown} />
          </div>
        )}
      </div>

      {/* Action Footer: Execute or Revise */}
      {canEdit && plan.status !== "accepted" && !editing && (
        <div className="plan-dock-actions">
          <div className="plan-dock-execute-row">
            <Button
              type="button"
              variant="primary"
              size="medium"
              disabled={disabled}
              loading={busy}
              onClick={handleExecute}
            >
              确认并执行计划
            </Button>
          </div>

          <div className="plan-dock-revise-row">
            <label className="plan-dock-revise-label">
              <span>提出修改意见</span>
              <div className="plan-dock-revise-input-group">
                <textarea
                  className="plan-dock-revise-textarea"
                  rows={2}
                  maxLength={8000}
                  value={feedback}
                  placeholder="说明需要调整的内容，模型将重新规划…"
                  disabled={disabled}
                  onChange={(e) => setFeedback(e.target.value)}
                />
                <Button
                  type="button"
                  variant="secondary"
                  size="small"
                  disabled={disabled || !feedback.trim()}
                  loading={busy}
                  onClick={handleRevise}
                >
                  发送修改要求
                </Button>
              </div>
            </label>
          </div>
        </div>
      )}

      {/* Revision History */}
      {historyPlans.length > 0 && (
        <div className="plan-dock-history">
          <details>
            <summary className="plan-dock-history-summary">
              历史版本 ({historyPlans.length})
            </summary>
            <div className="plan-dock-history-list">
              {historyPlans.map((previous) => (
                <details key={`${previous.id}:${previous.revision}`} className="plan-dock-history-item">
                  <summary>
                    {previous.title} · 版本 {previous.revision}
                  </summary>
                  <div className="plan-dock-history-content">
                    <MarkdownContent content={previous.markdown} />
                  </div>
                </details>
              ))}
            </div>
          </details>
        </div>
      )}
    </div>
  );
}
