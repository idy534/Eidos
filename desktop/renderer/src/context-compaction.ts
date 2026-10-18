import type { ContextCompactionActivity } from "../../shared/context.generated.js";

export function mergeContextCompactions(
  current: readonly ContextCompactionActivity[] = [],
  incoming: readonly ContextCompactionActivity[] = [],
): ContextCompactionActivity[] {
  const entries = new Map<string, ContextCompactionActivity>();
  for (const activity of [...current, ...incoming]) {
    const key = `${activity.runId}:${activity.requestId}`;
    const previous = entries.get(key);
    const latest = previous && previous.eventId >= activity.eventId ? previous : activity;
    entries.set(key, previous
      ? { ...latest, startedAt: Math.min(previous.startedAt, activity.startedAt) }
      : activity);
  }
  return [...entries.values()].sort((a, b) => a.startedAt - b.startedAt || a.eventId - b.eventId);
}
