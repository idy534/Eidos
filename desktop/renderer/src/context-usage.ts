import type { ContextUsage } from "./contracts.js";
import type { ContextDetails } from "../../shared/context-management.js";

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
  const percent = `${usage.percentUsed.toFixed(1).replace(/\.0$/, "")}%`;
  const estimated = usage.source === "estimated";
  const marker = estimated ? "≈" : "";
  return `上下文 ${marker}${percent} · ${marker}${formatTokenCount(usage.activeTokens)} / ${formatTokenCount(usage.windowTokens)}`;
}

export function contextUsageFromDetails(details: ContextDetails): ContextUsage | undefined {
  if (details.activeTokens == null) return undefined;
  return {
    activeTokens: details.activeTokens, windowTokens: details.contextWindowTokens,
    percentUsed: Math.round(details.activeTokens / details.contextWindowTokens * 1000) / 10,
    source: details.measurement === "provider" ? "provider" : "estimated", updatedAt: details.updatedAt ?? 0,
  };
}

/** Rounded window shares add up to the displayed total percentage. */
export function contextSectionPercentages(details: ContextDetails): Map<string, number> {
  const sections = details.sections ?? [];
  const total = sections.reduce((sum, section) => sum + section.tokens, 0);
  const target = Math.round(total / details.contextWindowTokens * 1000);
  const values = sections.map((section) => total ? section.tokens / total * target : 0);
  const units = values.map(Math.floor);
  const order = values.map((value, index) => ({ index, remainder: value - units[index]! }))
    .sort((a, b) => b.remainder - a.remainder);
  for (const { index } of order.slice(0, target - units.reduce((sum, value) => sum + value, 0))) units[index] = units[index]! + 1;
  return new Map(sections.map((section, index) => [section.name, units[index]! / 10]));
}

function trimUnit(value: number): string {
  return value.toFixed(1).replace(/\.0$/, "");
}
