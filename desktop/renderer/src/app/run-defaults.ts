import type { ApprovalMode, ModelId, Run } from "../contracts.js";

const RUN_DEFAULTS_KEY = "eidos.runDefaults.v1";
const APPROVAL_MODES = new Set<ApprovalMode>(["manual", "auto_review", "full_access"]);

export interface RunDefaults {
  modelId?: ModelId;
  approvalMode?: ApprovalMode;
}

export function loadRunDefaults(): RunDefaults {
  try {
    const value: unknown = JSON.parse(window.localStorage.getItem(RUN_DEFAULTS_KEY) ?? "null");
    if (!value || typeof value !== "object" || Array.isArray(value)) return {};

    const stored = value as Record<string, unknown>;
    return {
      ...(typeof stored.modelId === "string" && stored.modelId.length > 0 && stored.modelId.length <= 256
        ? { modelId: stored.modelId }
        : {}),
      ...(typeof stored.approvalMode === "string" && APPROVAL_MODES.has(stored.approvalMode as ApprovalMode)
        ? { approvalMode: stored.approvalMode as ApprovalMode }
        : {}),
    };
  } catch {
    return {};
  }
}

export function saveRunDefaults(run: Pick<Run, "modelId" | "approvalMode">): RunDefaults {
  const defaults: RunDefaults = {
    modelId: run.modelId,
    approvalMode: run.approvalMode ?? "manual",
  };
  try {
    window.localStorage.setItem(RUN_DEFAULTS_KEY, JSON.stringify(defaults));
  } catch {
    // Run defaults are a non-critical UI preference.
  }
  return defaults;
}
