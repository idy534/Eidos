import type { ModelId, Run } from "../contracts.js";
import { findActiveRun } from "../session-state.js";

/**
 * Resolves the ModelId for a session based on its runs.
 *
 * Priority:
 * 1. Active Run returned by findActiveRun(runs)
 * 2. Most recently updated Run (sorted by updatedAt -> createdAt -> id)
 * 3. Most recently created Run
 * 4. undefined (if no runs exist)
 */
export function resolveSessionModelId(runs: Run[]): ModelId | undefined {
  if (!Array.isArray(runs) || runs.length === 0) {
    return undefined;
  }

  // 1. Active Run priority
  const activeRun = findActiveRun(runs);
  if (activeRun) {
    return activeRun.profileId ?? activeRun.modelId;
  }

  // 2 & 3. Most recently updated / created Run with deterministic tie-breaking
  const sorted = [...runs].sort((a, b) => {
    if (b.updatedAt !== a.updatedAt) {
      return b.updatedAt - a.updatedAt;
    }
    if (b.createdAt !== a.createdAt) {
      return b.createdAt - a.createdAt;
    }
    return b.id.localeCompare(a.id);
  });

  return sorted[0]?.profileId ?? sorted[0]?.modelId;
}
