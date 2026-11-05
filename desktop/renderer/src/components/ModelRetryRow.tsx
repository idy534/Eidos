import { useEffect, useState } from "react";
import type { ModelRetryActivity } from "../../../shared/context.generated.js";
import type { ModelOption, Run } from "../contracts.js";
import {
  modelRetryLabels,
  modelRetryPhase,
  resolveModelRetryTarget,
  retryCauseName,
  type ModelFailurePresentation,
  type ModelRetryCause,
  type ModelRetryPhase,
} from "../model-retry.js";

const iconProps = {
  viewBox: "0 0 16 16", width: 14, height: 14, fill: "none", stroke: "currentColor",
  strokeWidth: 1.3, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true,
} as const;

function CauseIcon({ cause }: { cause: ModelRetryCause }) {
  switch (cause) {
    case "connection_failed":
      return <svg {...iconProps}><path d="M2 6.2a8 8 0 0 1 3-1.8M14 6.2a8 8 0 0 0-4.2-2" /><path d="M4.4 8.7a4.8 4.8 0 0 1 1.7-1M11.6 8.7a4.8 4.8 0 0 0-2.4-1.3" /><path d="M8 11.8h.01" /><path d="M2.5 2.5l11 11" /></svg>;
    case "timeout":
      return <svg {...iconProps}><circle cx="8" cy="8" r="5.8" /><path d="M8 4.7V8l2.2 1.4" /></svg>;
    case "service_unavailable":
      return <svg {...iconProps}><rect x="2.2" y="2.6" width="11.6" height="4.2" rx="1.2" /><rect x="2.2" y="9.2" width="11.6" height="4.2" rx="1.2" /><path d="M5 4.7h.01M5 11.3h.01" /></svg>;
    case "rate_limited":
      return <svg {...iconProps}><path d="M2.4 11.6a5.8 5.8 0 1 1 11.2 0" /><path d="M8 9.6l2.4-3" /><path d="M8 9.6h.01" /></svg>;
    case "stream_interrupted":
      return <svg {...iconProps}><path d="M1.8 8h2l1.4-3.4 2.4 7 1.8-5 1 1.4h3.2" /></svg>;
  }
}

function RefreshIcon() {
  return <svg {...iconProps}><path d="M13 7.2A5 5 0 0 0 4 5.4M3 2.8v2.8h2.8" /><path d="M3 8.8A5 5 0 0 0 12 10.6M13 13.2v-2.8h-2.8" /></svg>;
}
function CheckIcon() {
  return <svg {...iconProps}><circle cx="8" cy="8" r="5.8" /><path d="M5.5 8.2l1.8 1.8 3.2-3.8" /></svg>;
}
function AlertIcon() {
  return <svg {...iconProps}><circle cx="8" cy="8" r="5.8" /><path d="M8 4.9V8.4M8 10.9h.01" /></svg>;
}

function useCountdown(target: number | undefined, live: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!live || target === undefined) return undefined;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, [live, target]);
  return now;
}

const ACTIVE_PHASES = new Set<ModelRetryPhase>(["waiting", "requesting"]);

/**
 * One stage row for a provider request that is being retried. The phase comes
 * from Runtime events and the Run status; this component never decides retries.
 */
export function ModelRetryRow({
  activity,
  run,
  models,
}: {
  activity: ModelRetryActivity;
  run: Run;
  models?: readonly ModelOption[] | undefined;
}) {
  const [open, setOpen] = useState(false);
  const live = ["queued", "running", "waiting_input", "waiting_agents", "waiting_approval", "finalizing"].includes(run.status);
  const now = useCountdown(activity.nextRetryAt ?? undefined, live && activity.status === "waiting");
  const phase = modelRetryPhase(activity, run, live ? now : Number.MAX_SAFE_INTEGER);
  const labels = modelRetryLabels(activity, phase);
  const active = ACTIVE_PHASES.has(phase);
  const remainingMs = phase === "waiting" && activity.nextRetryAt != null ? Math.max(0, activity.nextRetryAt - now) : 0;
  const totalMs = activity.nextRetryAt != null ? Math.max(1, activity.nextRetryAt - activity.updatedAt) : 1;
  const seconds = Math.max(1, Math.ceil(remainingMs / 1000));
  const icon = phase === "waiting" ? <CauseIcon cause={activity.cause} />
    : phase === "requesting" ? <RefreshIcon />
    : phase === "recovered" ? <CheckIcon />
    : <AlertIcon />;
  return (
    <div className="model-retry" data-phase={phase} data-live={active} data-retry-group={activity.requestGroupId}
      role={active ? "status" : undefined} aria-live={active ? "polite" : undefined}>
      <div className="model-retry-head">
        <span className="model-retry-icon" aria-hidden="true">{icon}</span>
        <span className="model-retry-label">{labels.title}</span>
        {labels.chip && <span className="model-retry-chip" key={`${activity.retryIndex}:${phase}`}>{labels.chip}</span>}
        {!active && (
          <button type="button" className="model-retry-link" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
            {open ? "收起" : "详情"}
          </button>
        )}
      </div>
      {active && (
        <div className="model-retry-meta">
          <span>
            {labels.caption}
            {phase === "waiting" && <> · <span className="model-retry-count">{seconds} 秒后重试</span></>}
          </span>
          <button type="button" className="model-retry-link" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
            {open ? "收起" : "详情"}
          </button>
        </div>
      )}
      {phase === "waiting" && (
        <div className="model-retry-rail" aria-hidden="true">
          <i key={activity.nextRetryAt ?? 0} style={{ animationDuration: `${totalMs}ms`, animationDelay: `-${Math.max(0, totalMs - remainingMs)}ms` }} />
        </div>
      )}
      {phase === "requesting" && <div className="model-retry-rail model-retry-rail--indeterminate" aria-hidden="true"><i /></div>}
      {open && <RetryDetails activity={activity} run={run} models={models} />}
    </div>
  );
}

function RetryDetails({
  activity,
  run,
  models,
}: {
  activity: ModelRetryActivity;
  run: Run;
  models?: readonly ModelOption[] | undefined;
}) {
  const elapsed = Math.max(0, Math.round((activity.updatedAt - activity.startedAt) / 1000));
  const { providerId, modelId } = resolveModelRetryTarget(activity, run, models);
  const rows: Array<[string, string]> = [
    ["原因", retryCauseName(activity.cause)],
    ...(activity.httpStatus != null ? [["HTTP 状态", String(activity.httpStatus)] as [string, string]] : []),
    ...(providerId ? [["服务商", providerId] as [string, string]] : []),
    ...(modelId ? [["模型", modelId] as [string, string]] : []),
    ["已持续", `${elapsed} 秒`],
  ];
  return (
    <dl className="model-retry-detail">
      {rows.map(([name, value]) => (
        <div key={name}><dt>{name}</dt><dd>{value}</dd></div>
      ))}
    </dl>
  );
}

export function ModelFailureNotice({
  presentation, onOpenModelSettings,
}: { presentation: ModelFailurePresentation; onOpenModelSettings?: (() => void) | undefined }) {
  const action = presentation.action;
  return (
    <div className={`model-failure model-failure--${presentation.tone}`} role="alert">
      <div className="model-failure-main">
        <span className="model-failure-icon" aria-hidden="true"><AlertIcon /></span>
        <div>
          <div className="model-failure-title">{presentation.title}</div>
          <p className="model-failure-body">{presentation.body}</p>
          {presentation.chips.length > 0 && (
            <div className="model-failure-chips">
              {presentation.chips.map((chip) => <span key={chip}>{chip}</span>)}
            </div>
          )}
        </div>
      </div>
      {action && onOpenModelSettings && (
        <div className="model-failure-actions">
          <button type="button" onClick={onOpenModelSettings}>{action.label}</button>
        </div>
      )}
    </div>
  );
}
