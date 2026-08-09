import type { ContextUsage } from "./contracts.js";

export type { ContextUsage } from "./contracts.js";

const THOUSAND = 1_000;
const MILLION = 1_000_000;

export function formatTokenCount(tokens: number): string {
  if (!Number.isFinite(tokens) || tokens < 0) {
    return "--";
  }
  if (tokens >= MILLION) {
    return `${trimUnit(tokens / MILLION)}M`;
  }
  if (tokens >= THOUSAND) {
    return `${trimUnit(tokens / THOUSAND)}K`;
  }
  return String(Math.round(tokens));
}

export function formatContextUsage(usage: ContextUsage | undefined): string {
  if (!usage) {
    return "上下文 --";
  }
  const percent = `${Math.round(Math.min(100, Math.max(0, usage.percentUsed)))}%`;
  const estimated = usage.source === "estimated";
  const marker = estimated ? "≈" : "";
  return `上下文 ${marker}${percent} · ${marker}${formatTokenCount(usage.activeTokens)} / ${formatTokenCount(usage.windowTokens)}`;
}

function trimUnit(value: number): string {
  return value.toFixed(1).replace(/\.0$/, "");
}
