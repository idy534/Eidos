import type { RunThinkingParams } from "../../shared/domain-contracts.js";

/** Live-only provider reasoning summary for one Run; never persisted, gone when the Run ends. */
export interface LiveThinking {
  summaryId: string;
  title: string;
  body?: string | undefined;
  /** When the Renderer received this part (local ms); a tool finishing later supersedes it. */
  receivedAt: number;
  /** Titles of earlier summary parts in this Run, oldest first. */
  previous: readonly string[];
}

export type LiveThinkingMap = Readonly<Record<string, LiveThinking>>;

export const MAX_PREVIOUS_THINKING = 4;

export function applyThinking(
  map: LiveThinkingMap,
  params: RunThinkingParams,
  now: number = Date.now(),
): LiveThinkingMap {
  const current = map[params.runId];
  const next: LiveThinking = { summaryId: params.summaryId, title: params.title, body: params.body, receivedAt: now, previous: [] };
  if (!current) return { ...map, [params.runId]: next };
  if (current.summaryId === params.summaryId) {
    if (current.title === params.title && current.body === params.body) return map;
    return { ...map, [params.runId]: { ...next, receivedAt: current.receivedAt, previous: current.previous } };
  }
  const previous = current.previous.at(-1) === current.title ? current.previous : [...current.previous, current.title];
  return { ...map, [params.runId]: { ...next, previous: previous.slice(-MAX_PREVIOUS_THINKING) } };
}

export function clearThinking(map: LiveThinkingMap, runId: string): LiveThinkingMap {
  if (!(runId in map)) return map;
  const { [runId]: _removed, ...rest } = map;
  return rest;
}
