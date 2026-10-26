import type { InputReference, InputReferenceKind } from "./input-context.js";

const INLINE_REF_TOKEN_SOURCE = "\\[([^\\[\\]\\r\\n]+)\\]\\(ref:([A-Za-z0-9_-]{1,64})\\)";

function matchesReferenceTokenId(referenceId: string, tokenRefId: string): boolean {
  return referenceId === tokenRefId || (tokenRefId.length >= 8 && referenceId.startsWith(tokenRefId));
}

export function isShelfReferenceKind(kind: InputReferenceKind): boolean {
  return (
    kind === "image"
    || kind === "file"
    || kind === "directory"
    || kind === "excerpt"
  );
}

export function isInlineReferenceKind(kind: InputReferenceKind): boolean {
  return !isShelfReferenceKind(kind);
}

export function isShelfReference(reference: InputReference): boolean {
  return isShelfReferenceKind(reference.kind);
}

export function isInlineReference(reference: InputReference): boolean {
  return isInlineReferenceKind(reference.kind);
}

export function sanitizeInlineReferenceLabel(label: string): string {
  return label
    .replace(/\[/g, "［")
    .replace(/\]/g, "］")
    .replace(/[\r\n]+/g, " ")
    .trim();
}

export function formatInlineReferenceToken(reference: InputReference): string {
  const prefix = reference.kind === "skill" ? "$" : "@";
  const safeLabel = sanitizeInlineReferenceLabel(reference.label) || reference.kind;
  const shortId = reference.id.slice(0, 12);
  return `[${prefix}${safeLabel}](ref:${shortId})`;
}

export type InlineSegment =
  | {
      type: "text";
      text: string;
      start: number;
      end: number;
    }
  | {
      type: "reference";
      reference: InputReference;
      raw: string;
      start: number;
      end: number;
    };

export function canonicalizeInlineText(
  text: string,
  references: readonly InputReference[],
): string {
  const inlineRefs = references.filter(isInlineReference);
  if (inlineRefs.length === 0) return text;

  const placedIds = new Set<string>();
  const pattern = new RegExp(INLINE_REF_TOKEN_SOURCE, "g");
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) !== null) {
    const refPrefix = match[2]!;
    const matched = inlineRefs.find((ref) => matchesReferenceTokenId(ref.id, refPrefix));
    if (matched) placedIds.add(matched.id);
  }

  const unplaced = inlineRefs.filter((ref) => !placedIds.has(ref.id));
  if (unplaced.length === 0) return text;

  const prefixTokens = unplaced.map(formatInlineReferenceToken).join(" ");
  if (!text) return prefixTokens;
  const needsSpace = !/^\s/.test(text);
  return `${prefixTokens}${needsSpace ? " " : ""}${text}`;
}

export function parseInlineSegments(
  text: string,
  references: readonly InputReference[],
  options?: { canonicalizeUnplaced?: boolean },
): InlineSegment[] {
  const inlineRefs = references.filter(isInlineReference);
  if (inlineRefs.length === 0) {
    return text ? [{ type: "text", text, start: 0, end: text.length }] : [];
  }

  const sourceText = options?.canonicalizeUnplaced === false
    ? text
    : canonicalizeInlineText(text, inlineRefs);

  const segments: InlineSegment[] = [];
  const pattern = new RegExp(INLINE_REF_TOKEN_SOURCE, "g");
  let cursor = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(sourceText)) !== null) {
    const fullMatch = match[0];
    const refPrefix = match[2]!;
    const matchStart = match.index;
    const matchEnd = matchStart + fullMatch.length;
    const matched = inlineRefs.find((ref) => matchesReferenceTokenId(ref.id, refPrefix));

    if (!matched) continue;

    if (matchStart > cursor) {
      segments.push({
        type: "text",
        text: sourceText.slice(cursor, matchStart),
        start: cursor,
        end: matchStart,
      });
    }
    segments.push({
      type: "reference",
      reference: matched,
      raw: fullMatch,
      start: matchStart,
      end: matchEnd,
    });
    cursor = matchEnd;
  }

  if (cursor < sourceText.length) {
    segments.push({
      type: "text",
      text: sourceText.slice(cursor),
      start: cursor,
      end: sourceText.length,
    });
  }

  return segments;
}

export function hasAnyInlineReferenceToken(text: string): boolean {
  return new RegExp(INLINE_REF_TOKEN_SOURCE).test(text);
}

export function containsInlineReferenceToken(text: string, referenceId: string): boolean {
  const pattern = new RegExp(INLINE_REF_TOKEN_SOURCE, "g");
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) !== null) {
    const refPrefix = match[2]!;
    if (matchesReferenceTokenId(referenceId, refPrefix)) return true;
  }
  return false;
}

export function removeInlineReferenceTokenById(text: string, referenceId: string): string {
  const pattern = new RegExp(INLINE_REF_TOKEN_SOURCE, "g");
  return text.replace(pattern, (fullMatch, _label: string, refPrefix: string) => (
    matchesReferenceTokenId(referenceId, refPrefix) ? "" : fullMatch
  ));
}

export function replaceInlineReferenceTokenId(
  text: string,
  oldReferenceId: string,
  nextReference: InputReference,
): string {
  const replacement = formatInlineReferenceToken(nextReference);
  const pattern = new RegExp(INLINE_REF_TOKEN_SOURCE, "g");
  return text.replace(pattern, (fullMatch, _label: string, refPrefix: string) => (
    matchesReferenceTokenId(oldReferenceId, refPrefix) ? replacement : fullMatch
  ));
}

export function insertInlineReferenceAtSelection(
  text: string,
  reference: InputReference,
  range?: { start: number; end: number },
): { text: string; caretOffset: number } {
  const token = formatInlineReferenceToken(reference);
  const start = Math.max(0, Math.min(text.length, range?.start ?? text.length));
  const end = Math.max(start, Math.min(text.length, range?.end ?? start));
  const before = text.slice(0, start);
  const after = text.slice(end);
  const nextText = `${before}${token}${after}`;
  return {
    text: nextText,
    caretOffset: before.length + token.length,
  };
}

export function formatInlineReferencesAsPlainText(text: string): string {
  const pattern = new RegExp(INLINE_REF_TOKEN_SOURCE, "g");
  return text.replace(pattern, (_fullMatch, label: string) => label);
}
