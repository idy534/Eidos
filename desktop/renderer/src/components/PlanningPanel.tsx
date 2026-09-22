import { useEffect, useRef, useState } from "react";
import type { InputAnswer, PlanDocument, PlanningReadResponse, UserInputRequest } from "../../../shared/planning.generated.js";
import { userFacingError } from "../session-state.js";
import { MarkdownContent } from "./MarkdownContent.js";
import "./planning.css";

export function PlanningPanel({ sessionId, ready, canEdit, onExecute, onRevise }: {
  sessionId: string;
  ready: boolean;
  canEdit: boolean;
  onExecute: (plan: PlanDocument) => Promise<boolean>;
  onRevise: (plan: PlanDocument, feedback: string) => Promise<boolean>;
}) {
  const [state, setState] = useState<PlanningReadResponse>();
  const [error, setError] = useState("");
  const refresh = useRef<() => void>(() => {});
  useEffect(() => {
    if (!ready) return;
    let disposed = false;
    let generation = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = () => {
      const current = ++generation;
      void window.eidosRuntime.readPlanning(sessionId).then((value) => {
        if (!disposed && current === generation) { setState(value); setError(""); }
      }).catch((cause: unknown) => {
        if (!disposed && current === generation) setError(userFacingError(cause));
      });
    };
    refresh.current = load;
    load();
    const unsubscribe = window.eidosRuntime.onNotification(() => {
      if (timer !== undefined) return;
      timer = setTimeout(() => { timer = undefined; load(); }, 250);
    });
    return () => { disposed = true; clearTimeout(timer); unsubscribe(); refresh.current = () => {}; };
  }, [sessionId, ready]);
  const pending = state?.questions.find((request) => request.status === "pending");
  const plan = state?.plans[0];
  if (!pending && !plan && !error) return null;
  return <section className="planning-panel" aria-label="计划与澄清">
    {error && <p role="alert">{error} <button type="button" onClick={() => refresh.current()}>重试</button></p>}
    {pending && <Questions key={pending.id} request={pending} ready={ready} onSaved={() => refresh.current()} />}
    {plan && <PlanCard key={`${plan.id}:${plan.revision}`} plan={plan} canEdit={ready && canEdit}
      onSaved={() => refresh.current()} onExecute={onExecute} onRevise={onRevise} />}
    {!!state?.questions.some((request) => request.status !== "pending") && <details>
      <summary>澄清记录</summary>
      {state.questions.filter((request) => request.status !== "pending").map((request) => <div key={request.id}>
        {request.questions.map((question) => {
          const answer = request.response?.answers?.find((value) => value.questionId === question.id);
          return <p key={question.id}><strong>{question.question}</strong><br />
            {request.status === "skipped" ? "已跳过" : request.status === "canceled" ? "已取消" : [
              ...(answer?.optionIds ?? []).map((id) => question.options?.find((option) => option.id === id)?.label ?? id), answer?.text,
            ].filter(Boolean).join("；")}</p>;
        })}
      </div>)}
    </details>}
    {(state?.plans.length ?? 0) > 1 && <details><summary>历史计划</summary>
      {state?.plans.slice(1).map((previous) => <details key={previous.id}><summary>{previous.title} · 版本 {previous.revision}</summary>
        <MarkdownContent content={previous.markdown} />
      </details>)}
    </details>}
  </section>;
}

function Questions({ request, ready, onSaved }: { request: UserInputRequest; ready: boolean; onSaved: () => void }) {
  const [answers, setAnswers] = useState<Record<string, InputAnswer>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const lock = useRef(false);
  const update = (id: string, patch: Partial<InputAnswer>) => setAnswers((previous) => ({
    ...previous, [id]: { questionId: id, ...previous[id], ...patch },
  }));
  const submit = async (skip: boolean) => {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError("");
    try {
      await window.eidosRuntime.answerUserInput({ requestId: request.id, response: {
        status: skip ? "skipped" : "answered",
        answers: skip ? [] : request.questions.map((question) => answers[question.id] ?? { questionId: question.id }),
      } });
      onSaved();
    } catch (cause) { setError(userFacingError(cause)); }
    finally { lock.current = false; setBusy(false); }
  };
  return <form onSubmit={(event) => { event.preventDefault(); void submit(false); }}>
    <h3>需要你补充信息</h3>
    {request.questions.map((question) => <fieldset key={question.id} disabled={busy || !ready}>
      <legend>{question.question}</legend>
      {question.options?.map((option) => <label key={option.id} className="planning-choice">
        <input type={question.type === "multi_select" ? "checkbox" : "radio"} name={`${request.id}:${question.id}`}
          checked={answers[question.id]?.optionIds?.includes(option.id) ?? false}
          onChange={(event) => update(question.id, { optionIds: question.type === "multi_select"
            ? event.target.checked ? [...(answers[question.id]?.optionIds ?? []), option.id] : (answers[question.id]?.optionIds ?? []).filter((id) => id !== option.id)
            : [option.id] })} />
        <span>{option.label}{question.recommendedOptionId === option.id ? "（推荐）" : ""}
          {option.description && <small>{option.description}</small>}</span>
      </label>)}
      <label>{question.type === "text" ? "你的回答" : "补充或自定义回答"}
        <textarea maxLength={8000} value={answers[question.id]?.text ?? ""} onChange={(event) => update(question.id, { text: event.target.value })} />
      </label>
    </fieldset>)}
    {error && <p role="alert">{error}</p>}
    <div className="planning-actions"><button type="submit" disabled={!ready || busy}>提交回答</button>
      <button type="button" disabled={!ready || busy} onClick={() => void submit(true)}>跳过</button></div>
  </form>;
}

function PlanCard({ plan, canEdit, onSaved, onExecute, onRevise }: {
  plan: PlanDocument; canEdit: boolean; onSaved: () => void;
  onExecute: (plan: PlanDocument) => Promise<boolean>;
  onRevise: (plan: PlanDocument, feedback: string) => Promise<boolean>;
}) {
  const [editing, setEditing] = useState(false);
  const [markdown, setMarkdown] = useState(plan.markdown);
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const lock = useRef(false);
  const perform = async (action: () => Promise<unknown>) => {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError("");
    try { await action(); onSaved(); } catch (cause) { setError(userFacingError(cause)); }
    finally { lock.current = false; setBusy(false); }
  };
  const disabled = busy || !canEdit || plan.status === "accepted";
  return <div>
    <h3>{plan.title} <small>版本 {plan.revision} · {plan.status === "review" ? "待确认" : plan.status === "accepted" ? "已确认执行" : "草稿"}</small></h3>
    <details open={plan.status !== "accepted"}><summary>计划正文</summary>
      {editing ? <label>Markdown 计划<textarea className="plan-editor" maxLength={65536} value={markdown} disabled={disabled} onChange={(event) => setMarkdown(event.target.value)} /></label>
        : <MarkdownContent content={plan.markdown} />}
    </details>
    {error && <p role="alert">{error}</p>}
    <div className="planning-actions">
      <button type="button" disabled={busy || !canEdit} onClick={() => void perform(() => window.eidosRuntime.openPlan(plan.id))}>打开 MD 文件</button>
      {plan.status !== "accepted" && <>
        <button type="button" disabled={disabled || editing} onClick={() => void perform(() => window.eidosRuntime.readPlan(plan.id, true))}>载入文件修改</button>
        <button type="button" disabled={disabled} onClick={() => { setMarkdown(plan.markdown); setEditing(!editing); }}>{editing ? "取消编辑" : "编辑计划"}</button>
        {editing && <button type="button" disabled={disabled || !markdown.trim()} onClick={() => void perform(async () => {
          await window.eidosRuntime.editPlan({ planId: plan.id, expectedRevision: plan.revision, markdown }); setEditing(false);
        })}>保存修改</button>}
        <button type="button" disabled={disabled || editing || plan.status !== "review"} onClick={() => void perform(() => onExecute(plan))}>确认并执行此版本</button>
      </>}
    </div>
    {plan.status !== "accepted" && <form onSubmit={(event) => { event.preventDefault(); void perform(() => onRevise(plan, feedback)); }}>
      <label>修改意见<textarea disabled={disabled || editing} value={feedback} maxLength={8000} onChange={(event) => setFeedback(event.target.value)} /></label>
      <button type="submit" disabled={disabled || editing || !feedback.trim()}>让模型修改计划</button>
    </form>}
  </div>;
}
