import type { RunTermination } from "./run-termination.generated.js";

export function isRunTermination(value: unknown): value is RunTermination {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const fact = value as Record<string, unknown>;
  const text = (v: unknown, max: number) => typeof v === "string" && v.length > 0 && v.length <= max;
  return Object.keys(fact).every((key) => [
    "stage", "reason", "stepId", "modelAttemptId", "toolName", "validationPath", "httpStatus",
  ].includes(key))
    && ["model", "tool_validation", "context", "runtime", "lifecycle"].includes(String(fact.stage))
    && text(fact.reason, 128)
    && [fact.stepId, fact.modelAttemptId, fact.toolName].every((v) => v == null || text(v, 128))
    && (fact.validationPath == null || text(fact.validationPath, 256))
    && (fact.httpStatus == null || (Number.isInteger(fact.httpStatus) && Number(fact.httpStatus) >= 100 && Number(fact.httpStatus) <= 599));
}
