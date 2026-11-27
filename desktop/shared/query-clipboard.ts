import type { InputDraft, InputReference } from "./input-context.js";
import { isInputReference } from "./input-context.js";
import {
  canonicalizeInlineText,
  containsInlineReferenceToken,
  formatInlineReferencesAsPlainText,
  isInlineReference,
  isShelfReference,
  parseInlineSegments,
  replaceInlineReferenceTokenId,
  replaceInlineReferenceTokenWithPlainText,
  sanitizeInlineReferenceLabel,
} from "./inline-references.js";

export const QUERY_CLIPBOARD_MIME = "application/x-eidos-query-clipboard";
export const MAX_DRAFT_REFERENCES = 20;
export const MAX_DRAFT_TEXT_LENGTH = 65536;

export interface QueryClipboardPayload {
  version: 1;
  text: string;
  references: InputReference[];
}

export function isSameReferenceTarget(
  existing: InputReference,
  incoming: InputReference,
): boolean {
  if (existing.id === incoming.id) return true;
  if (
    existing.source === "clipboard:image"
    || incoming.source === "clipboard:image"
    || existing.kind === "excerpt"
    || incoming.kind === "excerpt"
  ) {
    return false;
  }
  return (
    existing.kind === incoming.kind
    && existing.source === incoming.source
    && existing.label === incoming.label
  );
}

export function isQueryClipboardPayload(value: unknown): value is QueryClipboardPayload {
  if (!value || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  const keys = Object.keys(record);
  return (
    keys.length === 3
    && keys.every((key) => key === "version" || key === "text" || key === "references")
    && record.version === 1
    && typeof record.text === "string"
    && record.text.length <= MAX_DRAFT_TEXT_LENGTH
    && Array.isArray(record.references)
    && record.references.length <= MAX_DRAFT_REFERENCES
    && record.references.every(isInputReference)
  );
}

export function serializeQueryClipboardPayload(payload: QueryClipboardPayload): string {
  return JSON.stringify(payload);
}

export function parseQueryClipboardPayload(raw: string): QueryClipboardPayload | undefined {
  if (!raw) return undefined;
  try {
    const parsed: unknown = JSON.parse(raw);
    return isQueryClipboardPayload(parsed) ? parsed : undefined;
  } catch {
    return undefined;
  }
}

export function formatShelfReferencePlainText(reference: InputReference): string {
  const safeLabel = sanitizeInlineReferenceLabel(reference.label) || reference.kind;
  switch (reference.kind) {
    case "file":
      return `[文件: ${safeLabel}]`;
    case "directory":
      return `[文件夹: ${safeLabel}]`;
    case "image":
      return `[图片: ${safeLabel}]`;
    case "excerpt":
      return `[引用: ${safeLabel}]`;
    default:
      return `[${safeLabel}]`;
  }
}

function deduplicateReferencesById(references: readonly InputReference[]): InputReference[] {
  const seen = new Set<string>();
  const unique: InputReference[] = [];
  for (const reference of references) {
    if (seen.has(reference.id)) continue;
    seen.add(reference.id);
    unique.push(reference);
    if (unique.length >= MAX_DRAFT_REFERENCES) break;
  }
  return unique;
}

function truncateAtInlineTokenBoundary(
  text: string,
  references: readonly InputReference[],
  maxLength: number,
): string {
  if (maxLength <= 0) return "";
  if (text.length <= maxLength) return text;
  const segments = parseInlineSegments(text, references, { canonicalizeUnplaced: false });
  let out = "";
  for (const segment of segments) {
    if (out.length >= maxLength) break;
    if (segment.type === "text") {
      out += segment.text.slice(0, maxLength - out.length);
      continue;
    }
    if (out.length + segment.raw.length <= maxLength) {
      out += segment.raw;
    } else {
      break;
    }
  }
  return out;
}

function snapSelectionToInlineTokenBoundaries(
  baseText: string,
  references: readonly InputReference[],
  start: number,
  end: number,
): { start: number; end: number } {
  const segments = parseInlineSegments(baseText, references, { canonicalizeUnplaced: false });
  let nextStart = start;
  let nextEnd = end;
  for (const segment of segments) {
    if (segment.type !== "reference") continue;
    if (nextStart === nextEnd && segment.start < nextStart && nextStart < segment.end) {
      return { start: segment.end, end: segment.end };
    }
    if (segment.start < nextStart && nextStart < segment.end) {
      nextStart = segment.start;
    }
    if (segment.start < nextEnd && nextEnd < segment.end) {
      nextEnd = segment.end;
    }
  }
  return { start: nextStart, end: Math.max(nextStart, nextEnd) };
}

export function buildQueryClipboardPayload(
  text: string,
  references: readonly InputReference[],
): QueryClipboardPayload {
  const uniqueReferences = deduplicateReferencesById(references);
  const canonicalText = canonicalizeInlineText(text, uniqueReferences);
  const boundedText = truncateAtInlineTokenBoundary(
    canonicalText,
    uniqueReferences,
    MAX_DRAFT_TEXT_LENGTH,
  );
  const boundedReferences = uniqueReferences.filter(
    (reference) => !isInlineReference(reference) || containsInlineReferenceToken(boundedText, reference.id),
  );
  return {
    version: 1,
    text: boundedText,
    references: boundedReferences,
  };
}

export function formatQueryAsPlainText(
  text: string,
  references: readonly InputReference[],
): string {
  const canonical = canonicalizeInlineText(text, references);
  const plainBody = formatInlineReferencesAsPlainText(canonical);
  const shelfRefs = references.filter(isShelfReference);
  if (shelfRefs.length === 0) return plainBody;
  const shelfLine = shelfRefs.map(formatShelfReferencePlainText).join(" ");
  return plainBody ? `${shelfLine}\n${plainBody}` : shelfLine;
}

export function degradeInvalidQueryReferences(
  payload: QueryClipboardPayload,
  invalidReferenceIds: ReadonlySet<string>,
): { text: string; references: InputReference[]; degradedCount: number } {
  if (invalidReferenceIds.size === 0) {
    return {
      text: payload.text,
      references: [...payload.references],
      degradedCount: 0,
    };
  }

  let nextText = canonicalizeInlineText(payload.text, payload.references);
  const validReferences: InputReference[] = [];
  const degradedShelfLabels: string[] = [];
  let degradedCount = 0;

  for (const reference of payload.references) {
    if (!invalidReferenceIds.has(reference.id)) {
      validReferences.push(reference);
      continue;
    }
    degradedCount += 1;
    if (isInlineReference(reference)) {
      nextText = replaceInlineReferenceTokenWithPlainText(nextText, reference);
    } else {
      degradedShelfLabels.push(formatShelfReferencePlainText(reference));
    }
  }

  if (degradedShelfLabels.length > 0) {
    const shelfPrefix = degradedShelfLabels.join(" ");
    nextText = nextText ? `${shelfPrefix}\n${nextText}` : shelfPrefix;
  }

  return {
    text: nextText,
    references: validReferences,
    degradedCount,
  };
}

export function mergePastedQueryIntoDraft(
  draft: InputDraft,
  pasted: { text: string; references: readonly InputReference[] },
  range?: { start: number; end: number },
): { draft: InputDraft; caretOffset: number; overflowDegradedCount: number } {
  const baseText = canonicalizeInlineText(draft.text, draft.references);
  const clampedStart = Math.max(0, Math.min(baseText.length, range?.start ?? baseText.length));
  const clampedEnd = Math.max(clampedStart, Math.min(baseText.length, range?.end ?? clampedStart));
  const { start, end } = snapSelectionToInlineTokenBoundaries(
    baseText,
    draft.references,
    clampedStart,
    clampedEnd,
  );
  const before = baseText.slice(0, start);
  const after = baseText.slice(end);
  const remainingText = `${before}${after}`;

  const nextReferences: InputReference[] = draft.references.filter(
    (reference) => !isInlineReference(reference) || containsInlineReferenceToken(remainingText, reference.id),
  );

  let pastedText = canonicalizeInlineText(pasted.text, pasted.references);
  const overflowShelfLabels: string[] = [];
  let overflowDegradedCount = 0;

  for (const incoming of pasted.references) {
    const existing = nextReferences.find((item) => isSameReferenceTarget(item, incoming));
    if (existing) {
      if (isInlineReference(incoming) && existing.id !== incoming.id) {
        pastedText = replaceInlineReferenceTokenId(pastedText, incoming.id, existing);
      }
      continue;
    }
    if (nextReferences.length >= MAX_DRAFT_REFERENCES) {
      overflowDegradedCount += 1;
      if (isInlineReference(incoming)) {
        pastedText = replaceInlineReferenceTokenWithPlainText(pastedText, incoming);
      } else {
        overflowShelfLabels.push(formatShelfReferencePlainText(incoming));
      }
      continue;
    }
    nextReferences.push(incoming);
  }

  if (overflowShelfLabels.length > 0) {
    const overflowPrefix = overflowShelfLabels.join(" ");
    pastedText = pastedText ? `${overflowPrefix}\n${pastedText}` : overflowPrefix;
  }

  const availableForPasted = Math.max(0, MAX_DRAFT_TEXT_LENGTH - before.length - after.length);
  const boundedPasted = truncateAtInlineTokenBoundary(
    pastedText,
    nextReferences,
    availableForPasted,
  );
  const combined = `${before}${boundedPasted}${after}`;
  const boundedText = combined.length > MAX_DRAFT_TEXT_LENGTH
    ? truncateAtInlineTokenBoundary(combined, nextReferences, MAX_DRAFT_TEXT_LENGTH)
    : combined;
  const caretOffset = Math.min(boundedText.length, before.length + boundedPasted.length);

  const finalReferences = nextReferences.filter(
    (reference) => !isInlineReference(reference) || containsInlineReferenceToken(boundedText, reference.id),
  );

  return {
    draft: {
      text: boundedText,
      references: finalReferences,
    },
    caretOffset,
    overflowDegradedCount,
  };
}

export function formatDegradationNotice(
  degradedCount: number,
  overflowDegradedCount: number,
): string | undefined {
  if (degradedCount > 0 && overflowDegradedCount > 0) {
    return "部分引用已更新、不可用或超过 20 个上限，已降级为纯文本。";
  }
  if (degradedCount > 0) {
    return "部分引用已更新或不可用，已降级为纯文本。";
  }
  if (overflowDegradedCount > 0) {
    return "每轮最多添加 20 个引用，超出部分已降级为纯文本。";
  }
  return undefined;
}
