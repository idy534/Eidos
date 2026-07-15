import type {
  Item,
  Run,
  RuntimeNotification,
  SessionSnapshot,
} from "./contracts.js";


export interface SnapshotReadToken {
  readonly generation: number;
  readonly sessionId: string;
}

export class SnapshotReadCoordinator {
  private generation = 0;
  private selectedSessionId: string | undefined;

  select(sessionId: string): SnapshotReadToken {
    this.selectedSessionId = sessionId;
    return this.nextToken(sessionId);
  }

  refresh(sessionId: string): SnapshotReadToken | undefined {
    if (this.selectedSessionId !== sessionId) {
      return undefined;
    }
    return this.nextToken(sessionId);
  }

  accept(
    token: SnapshotReadToken,
    snapshot: SessionSnapshot,
  ): SessionSnapshot | undefined {
    return this.isCurrent(token) && snapshot.session.id === token.sessionId
      ? snapshot
      : undefined;
  }

  isCurrent(token: SnapshotReadToken): boolean {
    return (
      token.generation === this.generation
      && token.sessionId === this.selectedSessionId
    );
  }

  private nextToken(sessionId: string): SnapshotReadToken {
    this.generation += 1;
    return { generation: this.generation, sessionId };
  }
}

export function applyNotification(
  snapshot: SessionSnapshot | undefined,
  notification: RuntimeNotification,
): SessionSnapshot | undefined {
  if (!snapshot || notification.params.sessionId !== snapshot.session.id) {
    return snapshot;
  }
  if (notification.method === "run/started" || notification.method === "run/completed") {
    return { ...snapshot, runs: upsertRun(snapshot.runs, notification.params.run) };
  }
  if (notification.method === "item/delta") {
    return {
      ...snapshot,
      items: snapshot.items.map((item) => item.id === notification.params.itemId
        ? { ...item, content: `${item.content ?? ""}${notification.params.delta}` }
        : item),
    };
  }
  const incoming = notification.params.item;
  const existing = snapshot.items.find((item) => item.id === incoming.id);
  let merged: Item = existing?.content !== undefined && incoming.content === undefined
    ? { ...incoming, content: existing.content }
    : incoming;
  if (existing?.toolCall && incoming.toolCall) {
    merged = { ...merged, toolCall: { ...existing.toolCall, ...incoming.toolCall } };
  }
  return { ...snapshot, items: upsertItem(snapshot.items, merged) };
}

export type RunStatusTone = "success" | "error" | "neutral" | "warning";

export interface RunStatusPresentation {
  label: string;
  tone: RunStatusTone;
}

export function terminalRunPresentation(
  run: Run,
): RunStatusPresentation | undefined {
  switch (run.status) {
    case "succeeded":
      return { label: "Run 已完成", tone: "success" };
    case "failed":
      return {
        label: `Run 失败：${run.errorCode ?? "UNKNOWN_ERROR"}`,
        tone: "error",
      };
    case "canceled":
      return { label: "Run 已取消", tone: "neutral" };
    case "interrupted":
      return { label: "Run 已中断，未自动恢复", tone: "warning" };
    case "running":
    case "waiting_approval":
      return undefined;
  }
}

function upsertRun(runs: Run[], incoming: Run): Run[] {
  const existing = runs.findIndex((run) => run.id === incoming.id);
  if (existing < 0) {
    return [...runs, incoming];
  }
  return runs.map((run, index) => index === existing ? incoming : run);
}

function upsertItem(items: Item[], incoming: Item): Item[] {
  const existing = items.findIndex((item) => item.id === incoming.id);
  if (existing < 0) {
    return [...items, incoming].sort((left, right) => left.ordinal - right.ordinal);
  }
  return items.map((item, index) => index === existing ? incoming : item);
}
