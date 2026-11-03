import type { ModelRetryActivity } from "../../shared/context.generated.js";
import type { Run } from "./contracts.js";

export type ModelRetryCause = ModelRetryActivity["cause"];
export type ModelRetryPhase = "waiting" | "requesting" | "recovered" | "failed" | "canceled";

const ACTIVE_RUN_STATUSES = new Set<Run["status"]>([
  "queued", "running", "waiting_input", "waiting_agents", "waiting_approval", "finalizing",
]);

export function mergeModelRetries(
  current: readonly ModelRetryActivity[] = [],
  incoming: readonly ModelRetryActivity[] = [],
): ModelRetryActivity[] {
  const entries = new Map<string, ModelRetryActivity>();
  for (const activity of [...current, ...incoming]) {
    const key = `${activity.runId}:${activity.requestGroupId}`;
    const previous = entries.get(key);
    const latest = previous && previous.eventId >= activity.eventId ? previous : activity;
    entries.set(key, previous
      ? { ...latest, startedAt: Math.min(previous.startedAt, activity.startedAt) }
      : activity);
  }
  return [...entries.values()].sort((a, b) => a.startedAt - b.startedAt || a.eventId - b.eventId);
}

/**
 * A waiting or requesting activity can outlive its Run (cancel, restart, a
 * deadline reached between events), so the displayed phase also reads the Run.
 */
export function modelRetryPhase(activity: ModelRetryActivity, run: Pick<Run, "status">, now: number): ModelRetryPhase {
  if (activity.status === "recovered" || activity.status === "failed") return activity.status;
  if (!ACTIVE_RUN_STATUSES.has(run.status)) return run.status === "canceled" ? "canceled" : "failed";
  if (activity.status === "waiting" && activity.nextRetryAt != null && now >= activity.nextRetryAt) return "requesting";
  return activity.status;
}

/** Number of retries already sent for this request; a waiting retry has not been sent yet. */
export function retriesSent(activity: ModelRetryActivity): number {
  return activity.status === "waiting" ? Math.max(0, activity.retryIndex - 1) : activity.retryIndex;
}

export function totalModelRetries(activities: readonly ModelRetryActivity[]): number {
  return activities.reduce((sum, activity) => sum + retriesSent(activity), 0);
}

const causeNames: Record<ModelRetryCause, string> = {
  connection_failed: "网络连接失败",
  timeout: "模型响应超时",
  service_unavailable: "模型服务暂时不可用",
  rate_limited: "请求被限流",
  stream_interrupted: "回复在接收过程中中断",
};

const waitingTitles: Record<ModelRetryCause, string> = {
  connection_failed: "网络连接失败，正在重试",
  timeout: "模型响应超时，正在重试",
  service_unavailable: "模型服务暂时不可用，正在重试",
  rate_limited: "请求被限流，等待后重试",
  stream_interrupted: "回复在接收过程中中断，正在重新请求",
};

export function retryCauseName(cause: ModelRetryCause): string {
  return causeNames[cause];
}

export interface ModelRetryLabels {
  title: string;
  /** Static explanation shown under the title; the countdown is rendered separately. */
  caption?: string | undefined;
  chip?: string | undefined;
}

export function modelRetryLabels(activity: ModelRetryActivity, phase: ModelRetryPhase): ModelRetryLabels {
  const index = activity.retryIndex;
  switch (phase) {
    case "waiting":
      return { title: waitingTitles[activity.cause], caption: waitingCaption(activity), chip: `第 ${index} 次重试` };
    case "requesting":
      return {
        title: `正在第 ${index} 次重试…`,
        caption: activity.cause === "stream_interrupted" ? "已收到的部分文字不会保留，将重新生成" : "请求已发出，等待模型服务响应",
      };
    case "recovered": {
      const seconds = Math.max(1, Math.round((activity.updatedAt - activity.startedAt) / 1000));
      return { title: `模型连接已恢复 · 共重试 ${index} 次，用时 ${formatSpan(seconds)}` };
    }
    case "canceled":
      return { title: `已取消 · 停止了第 ${Math.max(1, index)} 次重试` };
    case "failed":
      return { title: `重试 ${Math.max(0, retriesSent(activity))} 次后仍失败 · ${causeNames[activity.cause]}` };
  }
}

function waitingCaption(activity: ModelRetryActivity): string | undefined {
  if (activity.cause === "rate_limited") return activity.retryAfterApplied ? "按服务商要求的时间等待（Retry-After）" : "服务商限制了请求频率或用量";
  if (activity.cause === "stream_interrupted") return "已收到的部分文字不会保留，将重新生成";
  if (activity.httpStatus != null) return `服务商返回 ${activity.httpStatus}`;
  if (activity.cause === "timeout") return "等待回复超时";
  return "无法连接到模型服务";
}

function formatSpan(seconds: number): string {
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分`;
}

export type ModelFailureAction = "model_settings" | "none";

export interface ModelFailurePresentation {
  title: string;
  body: string;
  chips: string[];
  tone: "danger" | "warning";
  action?: { label: string; kind: Exclude<ModelFailureAction, "none"> } | undefined;
}

interface FailedRun {
  status: Run["status"];
  termination?: Run["termination"] | undefined;
  errorCode?: string | undefined;
}

/**
 * Structured copy for a Run that ended on a provider failure. Returns undefined
 * for every other failure so the ordinary one-line notice keeps handling them.
 */
export function modelFailurePresentation(
  run: FailedRun,
  retry?: ModelRetryActivity | undefined,
): ModelFailurePresentation | undefined {
  if (run.status !== "failed") return undefined;
  const reason = run.termination?.reason ?? "";
  const status = run.termination?.httpStatus ?? undefined;
  const chips = [reason, status ? `HTTP ${status}` : ""].filter(Boolean);
  const retried = retry && retry.status !== "recovered" ? retriesSent(retry) : 0;
  const retriedText = retried > 0 ? `已自动重试 ${retried} 次` : "";

  if (reason === "insufficient_quota") {
    return {
      title: "模型账户额度不足",
      body: "服务商明确返回余额或配额已用尽，重试不会改变结果，所以没有重试。充值或切换模型后，可以在当前会话继续发送消息。",
      chips, tone: "danger", action: { label: "打开模型设置", kind: "model_settings" },
    };
  }
  if (reason === "authentication_failed") {
    return {
      title: status === 403 ? "模型服务拒绝了访问" : "模型服务拒绝了 API Key",
      body: "这类问题重试不会改变结果，所以没有重试。请确认 Key 有效、属于当前 Base URL，并且账号可以使用这个模型。",
      chips, tone: "danger", action: { label: "打开模型设置", kind: "model_settings" },
    };
  }
  if (reason === "invalid_request" || run.errorCode === "MODEL_INVALID_REQUEST") {
    return {
      title: "模型服务拒绝了请求参数",
      body: "通常是模型名称、推理强度或输出上限不被该服务支持。重试不会改变结果，所以没有重试。",
      chips, tone: "danger", action: { label: "打开模型设置", kind: "model_settings" },
    };
  }
  if (reason === "content_filter") {
    return {
      title: "模型服务因内容限制终止了回复",
      body: "服务商的内容策略拦截了本次回复。可以调整措辞后重新发送，或换用其他模型。",
      chips, tone: "warning",
    };
  }
  if (["provider_unavailable", "provider_timeout", "rate_limited", "model_stream_interrupted", "stream_interrupted"].includes(reason)) {
    const cause = retry?.cause ?? causeFromReason(reason, status);
    return {
      title: retried > 0 ? `多次重试后仍无法完成请求 · ${causeNames[cause]}` : `${causeNames[cause]}，请求没有完成`,
      body: `${retriedText ? `${retriedText}，` : ""}模型服务仍然没有正常响应，任务已停止。已完成的文件修改和对话内容均保留，可以稍后继续发送消息。`,
      chips, tone: "danger", action: { label: "检查模型设置", kind: "model_settings" },
    };
  }
  return undefined;
}

function causeFromReason(reason: string, status: number | undefined): ModelRetryCause {
  if (reason === "rate_limited") return "rate_limited";
  if (reason === "model_stream_interrupted" || reason === "stream_interrupted") return "stream_interrupted";
  if (reason === "provider_timeout" || status === 408 || status === 504) return "timeout";
  if (status !== undefined) return "service_unavailable";
  return "connection_failed";
}
