export type ModelId = "deepseek-v4-flash" | "deepseek-v4-pro";

export const VALID_MODEL_IDS: ReadonlySet<string> = new Set<ModelId>([
  "deepseek-v4-flash",
  "deepseek-v4-pro",
]);

export const MAX_APPROVAL_FEEDBACK_BYTES = 2_000;
