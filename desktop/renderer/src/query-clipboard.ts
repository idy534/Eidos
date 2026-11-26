import { isInputReference, type InputReference } from "../../shared/input-context.js";
import {
  canonicalizeInlineText,
  containsInlineReferenceToken,
  isInlineReference,
  isShelfReference,
} from "../../shared/inline-references.js";
import {
  QUERY_CLIPBOARD_MIME,
  buildQueryClipboardPayload,
  degradeInvalidQueryReferences,
  formatQueryAsPlainText,
  parseQueryClipboardPayload,
  serializeQueryClipboardPayload,
  type QueryClipboardPayload,
} from "../../shared/query-clipboard.js";

export interface ClipboardDataLike {
  getData(format: string): string;
  setData(format: string, data: string): void;
  files?: ArrayLike<File> | undefined;
}

export interface ClipboardEventLike {
  preventDefault(): void;
  clipboardData: ClipboardDataLike | null;
}

let clipboardMirror: { plainText: string; payload: QueryClipboardPayload } | undefined;
let lastShiftPasteAt = 0;

export function clearQueryClipboardMirror(): void {
  clipboardMirror = undefined;
  lastShiftPasteAt = 0;
}

export function markShiftPasteIfMatching(event: {
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
  key: string;
}): void {
  if ((event.ctrlKey || event.metaKey) && event.shiftKey && event.key.toLowerCase() === "v") {
    lastShiftPasteAt = Date.now();
  }
}

export function consumeRecentShiftPaste(): boolean {
  if (lastShiftPasteAt === 0) return false;
  const recent = Date.now() - lastShiftPasteAt <= 300;
  lastShiftPasteAt = 0;
  return recent;
}

function normalizeLineEndings(value: string): string {
  return value.replace(/\r\n?/g, "\n");
}

export async function writeQueryToClipboard(
  text: string,
  references: readonly InputReference[],
  event?: ClipboardEventLike,
): Promise<boolean> {
  const payload = buildQueryClipboardPayload(text, references);
  const plainText = formatQueryAsPlainText(payload.text, payload.references);
  if (!plainText && payload.references.length === 0) return false;
  const normalizedPlainText = normalizeLineEndings(plainText);

  if (event?.clipboardData) {
    event.preventDefault();
    event.clipboardData.setData("text/plain", plainText);
    event.clipboardData.setData(QUERY_CLIPBOARD_MIME, serializeQueryClipboardPayload(payload));
    clipboardMirror = { plainText: normalizedPlainText, payload };
    return true;
  }

  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(plainText);
  }
  clipboardMirror = { plainText: normalizedPlainText, payload };
  return true;
}

export function readQueryFromClipboardEvent(
  event: ClipboardEventLike,
  options?: { preferPlainText?: boolean | undefined },
): QueryClipboardPayload | undefined {
  if (options?.preferPlainText) return undefined;
  const data = event.clipboardData;
  if (!data) return undefined;
  const structured = parseQueryClipboardPayload(data.getData(QUERY_CLIPBOARD_MIME));
  if (structured) return structured;
  const plain = data.getData("text/plain");
  if (clipboardMirror && normalizeLineEndings(plain) === clipboardMirror.plainText) {
    return {
      version: 1,
      text: clipboardMirror.payload.text,
      references: [...clipboardMirror.payload.references],
    };
  }
  return undefined;
}

function rangeEnclosesImage(range: Range, el: HTMLElement): boolean {
  const img = el.querySelector("img");
  if (!img) return false;
  try {
    const imgRange = document.createRange();
    imgRange.selectNode(img);
    return (
      range.compareBoundaryPoints(Range.START_TO_START, imgRange) <= 0
      && range.compareBoundaryPoints(Range.END_TO_END, imgRange) >= 0
    );
  } catch {
    return false;
  }
}

function selectionIntersectsNode(_selection: Selection, range: Range, node: Node): boolean {
  try {
    const contentRange = document.createRange();
    contentRange.selectNodeContents(node);
    if (
      range.compareBoundaryPoints(Range.END_TO_START, contentRange) >= 0
      || range.compareBoundaryPoints(Range.START_TO_END, contentRange) <= 0
    ) {
      return false;
    }
    if (node.nodeType === Node.ELEMENT_NODE) {
      const el = node as HTMLElement;
      const totalTextLen = (el.textContent ?? "").length;
      if (
        el.contains(range.endContainer)
        && textOffsetInsideElement(el, range.endContainer, range.endOffset) <= 0
        && !rangeEnclosesImage(range, el)
      ) {
        return false;
      }
      if (
        totalTextLen > 0
        && el.contains(range.startContainer)
        && textOffsetInsideElement(el, range.startContainer, range.startOffset) >= totalTextLen
        && !rangeEnclosesImage(range, el)
      ) {
        return false;
      }
    }
    return true;
  } catch {
    return false;
  }
}

function findInlineRefAncestor(node: Node | null, stopAt: Node): HTMLElement | null {
  let current: Node | null = node;
  while (current && current !== stopAt) {
    if (current.nodeType === Node.ELEMENT_NODE) {
      const element = current as HTMLElement;
      if (element.dataset.inlineRefRaw) return element;
    }
    current = current.parentNode;
  }
  return null;
}

function textOffsetInsideElement(rootEl: HTMLElement, target: Node, targetOffset: number): number {
  if (!rootEl.contains(target)) return 0;
  try {
    const prefixRange = document.createRange();
    prefixRange.setStart(rootEl, 0);
    if (target.nodeType === Node.TEXT_NODE) {
      const clamped = Math.max(0, Math.min(targetOffset, (target.nodeValue ?? "").length));
      prefixRange.setEnd(target, clamped);
    } else {
      const clamped = Math.max(0, Math.min(targetOffset, target.childNodes.length));
      prefixRange.setEnd(target, clamped);
    }
    return prefixRange.toString().length;
  } catch {
    return 0;
  }
}

function prosePointToCanonicalOffset(
  proseEl: HTMLElement,
  container: Node,
  offset: number,
  endSide: boolean,
): number {
  const children = Array.from(proseEl.childNodes);
  let acc = 0;

  if (container === proseEl) {
    const bound = Math.max(0, Math.min(offset, children.length));
    for (let i = 0; i < bound; i++) {
      const child = children[i]!;
      if (child.nodeType === Node.TEXT_NODE) {
        acc += (child.nodeValue ?? "").length;
      } else if (child.nodeType === Node.ELEMENT_NODE) {
        const el = child as HTMLElement;
        acc += el.dataset.inlineRefRaw ? el.dataset.inlineRefRaw.length : (el.textContent ?? "").length;
      }
    }
    return acc;
  }

  for (const child of children) {
    if (child === container) {
      if (child.nodeType === Node.TEXT_NODE) {
        return acc + Math.max(0, Math.min(offset, (child.nodeValue ?? "").length));
      }
      const el = child as HTMLElement;
      if (el.dataset.inlineRefRaw) {
        const rawLen = el.dataset.inlineRefRaw.length;
        return acc + (offset > 0 ? rawLen : 0);
      }
      return acc + textOffsetInsideElement(el, container, offset);
    }
    if (child.contains(container)) {
      const el = child as HTMLElement;
      if (el.dataset.inlineRefRaw) {
        const rawLen = el.dataset.inlineRefRaw.length;
        const totalTextLen = (el.textContent ?? "").length;
        if (totalTextLen === 0) {
          return acc + (endSide || offset > 0 ? rawLen : 0);
        }
        const innerOffset = textOffsetInsideElement(el, container, offset);
        if (endSide) {
          return acc + (innerOffset > 0 ? rawLen : 0);
        }
        return acc + (innerOffset >= totalTextLen ? rawLen : 0);
      }
      return acc + textOffsetInsideElement(el, container, offset);
    }
    if (child.nodeType === Node.TEXT_NODE) {
      acc += (child.nodeValue ?? "").length;
    } else if (child.nodeType === Node.ELEMENT_NODE) {
      const el = child as HTMLElement;
      acc += el.dataset.inlineRefRaw ? el.dataset.inlineRefRaw.length : (el.textContent ?? "").length;
    }
  }

  return acc;
}

export function extractSelectedQueryFromBubble(
  bubbleEl: HTMLElement,
  content: string,
  references: readonly InputReference[],
): { text: string; references: InputReference[] } | undefined {
  if (typeof window === "undefined" || references.length === 0) return undefined;
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) return undefined;
  const range = selection.getRangeAt(0);
  if (!bubbleEl.contains(range.startContainer) || !bubbleEl.contains(range.endContainer)) {
    return undefined;
  }

  const shelfRefs = references.filter(isShelfReference);
  const shelfById = new Map(shelfRefs.map((ref) => [ref.id, ref]));
  const selectedShelfRefs: InputReference[] = [];
  const seenShelfIds = new Set<string>();
  for (const card of Array.from(bubbleEl.querySelectorAll<HTMLElement>("[data-shelf-ref-id]"))) {
    const id = card.dataset.shelfRefId;
    if (!id || seenShelfIds.has(id)) continue;
    if (!selectionIntersectsNode(selection, range, card)) continue;
    const matched = shelfById.get(id);
    if (matched) {
      seenShelfIds.add(id);
      selectedShelfRefs.push(matched);
    }
  }

  const inlineRefs = references.filter(isInlineReference);
  const inlineProseEl = bubbleEl.querySelector<HTMLElement>(".eidos-inline-reference-prose");
  let rawSlice = "";
  let selectedInlineRefs: InputReference[] = [];

  if (inlineProseEl && selectionIntersectsNode(selection, range, inlineProseEl)) {
    const startInlineSpan = findInlineRefAncestor(range.startContainer, inlineProseEl);
    const endInlineSpan = findInlineRefAncestor(range.endContainer, inlineProseEl);

    if (startInlineSpan && startInlineSpan === endInlineSpan) {
      const totalLabelLen = (startInlineSpan.textContent ?? "").trim().length;
      const selectedLabelLen = selection.toString().trim().length;
      const expectedLabel = (startInlineSpan.dataset.inlineRefLabel ?? "").trim();
      const fullPillSelected =
        selectedLabelLen > 0
        && (
          selection.toString().trim() === expectedLabel
          || (totalLabelLen > 0 && selectedLabelLen >= totalLabelLen)
        );
      if (!fullPillSelected) {
        if (selectedShelfRefs.length === 0) return undefined;
      } else {
        const refId = startInlineSpan.dataset.inlineRefId;
        const matched = inlineRefs.find((ref) => ref.id === refId);
        rawSlice = startInlineSpan.dataset.inlineRefRaw ?? "";
        if (matched) selectedInlineRefs = [matched];
      }
    } else {
      const canonical = canonicalizeInlineText(content, inlineRefs);
      const proseRange = document.createRange();
      proseRange.selectNodeContents(inlineProseEl);

      let startOffset: number;
      if (range.startContainer === inlineProseEl || inlineProseEl.contains(range.startContainer)) {
        startOffset = prosePointToCanonicalOffset(inlineProseEl, range.startContainer, range.startOffset, false);
      } else if (range.compareBoundaryPoints(Range.START_TO_START, proseRange) < 0) {
        startOffset = 0;
      } else {
        startOffset = canonical.length;
      }

      let endOffset: number;
      if (range.endContainer === inlineProseEl || inlineProseEl.contains(range.endContainer)) {
        endOffset = prosePointToCanonicalOffset(inlineProseEl, range.endContainer, range.endOffset, true);
      } else if (range.compareBoundaryPoints(Range.END_TO_END, proseRange) > 0) {
        endOffset = canonical.length;
      } else {
        endOffset = 0;
      }

      if (endOffset > startOffset) {
        rawSlice = canonical.slice(startOffset, endOffset);
        selectedInlineRefs = inlineRefs.filter((ref) => containsInlineReferenceToken(rawSlice, ref.id));
      }
    }
  } else {
    const plainProseEl = bubbleEl.querySelector<HTMLElement>(".eidos-chat-prose");
    if (plainProseEl && selectionIntersectsNode(selection, range, plainProseEl)) {
      const subRange = document.createRange();
      subRange.selectNodeContents(plainProseEl);
      if (range.compareBoundaryPoints(Range.START_TO_START, subRange) > 0) {
        subRange.setStart(range.startContainer, range.startOffset);
      }
      if (range.compareBoundaryPoints(Range.END_TO_END, subRange) < 0) {
        subRange.setEnd(range.endContainer, range.endOffset);
      }
      rawSlice = subRange.toString();
    }
  }

  const selectedReferences = [...selectedShelfRefs, ...selectedInlineRefs];
  if (selectedReferences.length === 0) return undefined;
  return {
    text: rawSlice,
    references: selectedReferences,
  };
}

async function sha256HexUtf8(value: string): Promise<string | undefined> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) return undefined;
  try {
    const bytes = new TextEncoder().encode(value);
    const digest = await subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
  } catch {
    return undefined;
  }
}

export async function resolvePastedQueryReferences(
  payload: QueryClipboardPayload,
  options?: { exemptReferenceIds?: ReadonlySet<string> | undefined },
): Promise<{ text: string; references: InputReference[]; degradedCount: number }> {
  const exemptIds = options?.exemptReferenceIds;
  const toCheck = payload.references.filter((ref) => !exemptIds?.has(ref.id));
  if (toCheck.length === 0) {
    return {
      text: payload.text,
      references: [...payload.references],
      degradedCount: 0,
    };
  }

  const api = typeof window !== "undefined" ? window.eidosRuntime : undefined;
  const kinds = new Set(toCheck.map((ref) => ref.kind));

  const [agentResult, skillResult, pluginResult, mcpResult] = await Promise.all([
    kinds.has("agent") && typeof api?.listAgentTypes === "function"
      ? api.listAgentTypes().then(
          (value) => ({ ok: true as const, value }),
          () => ({ ok: false as const }),
        )
      : Promise.resolve(undefined),
    kinds.has("skill") && typeof api?.listSkills === "function"
      ? api.listSkills().then(
          (value) => ({ ok: true as const, value }),
          () => ({ ok: false as const }),
        )
      : Promise.resolve(undefined),
    kinds.has("plugin") && typeof api?.listPlugins === "function"
      ? api.listPlugins().then(
          (value) => ({ ok: true as const, value }),
          () => ({ ok: false as const }),
        )
      : Promise.resolve(undefined),
    kinds.has("mcp") && typeof api?.listMcpServers === "function"
      ? api.listMcpServers().then(
          (value) => ({ ok: true as const, value }),
          () => ({ ok: false as const }),
        )
      : Promise.resolve(undefined),
  ]);

  const checkReference = async (ref: InputReference): Promise<boolean> => {
    if (typeof api?.readInput === "function") {
      try {
        const preview = await api.readInput(ref.id);
        const authoritative = preview?.reference;
        if (
          !isInputReference(authoritative)
          || authoritative.id !== ref.id
          || authoritative.kind !== ref.kind
          || authoritative.source !== ref.source
          || authoritative.label !== ref.label
          || authoritative.sha256 !== ref.sha256
          || authoritative.status !== ref.status
          || authoritative.size !== ref.size
        ) {
          return false;
        }
      } catch {
        return false;
      }
    }

    switch (ref.kind) {
      case "file":
      case "directory":
      case "image":
      case "excerpt":
        return true;

      case "history": {
        if (typeof api?.readSession !== "function") return true;
        try {
          await api.readSession(ref.source, { itemLimit: 1 });
          return true;
        } catch {
          return false;
        }
      }

      case "agent": {
        if (!agentResult) return true;
        if (!agentResult.ok) return false;
        const match = (agentResult.value.agents ?? []).find((item) => item.name === ref.source);
        return Boolean(
          match
          && match.enabled === true
          && match.available === true
          && match.definitionVersion === ref.sha256,
        );
      }

      case "skill": {
        if (!skillResult) return true;
        if (!skillResult.ok) return false;
        const match = skillResult.value.skills.find((item) => item.qualifiedId === ref.source);
        if (!match || !match.enabled || !match.available) return false;
        const digest = await sha256HexUtf8(match.contentHash);
        if (digest !== undefined) return digest === ref.sha256;
        if (typeof api?.prepareInput === "function") {
          try {
            const prepared = await api.prepareInput({ kind: "skill", source: ref.source });
            return prepared.id === ref.id && prepared.sha256 === ref.sha256;
          } catch {
            return false;
          }
        }
        return false;
      }

      case "plugin": {
        if (!pluginResult) return true;
        if (!pluginResult.ok) return false;
        const match = pluginResult.value.plugins.find((item) => item.id === ref.source);
        if (!match || !match.enabled || match.status !== "installed") return false;
        const digest = await sha256HexUtf8(match.contentHash);
        if (digest !== undefined) return digest === ref.sha256;
        if (typeof api?.prepareInput === "function") {
          try {
            const prepared = await api.prepareInput({ kind: "plugin", source: ref.source });
            return prepared.id === ref.id && prepared.sha256 === ref.sha256;
          } catch {
            return false;
          }
        }
        return false;
      }

      case "mcp": {
        if (!mcpResult) return true;
        if (!mcpResult.ok) return false;
        const candidates = mcpResult.value.servers.filter(
          (item) => item.serverId === ref.source && item.available && item.consented,
        );
        if (candidates.length === 0) return false;
        if (typeof api?.prepareInput !== "function") return true;
        for (const candidate of candidates) {
          try {
            const prepared = await api.prepareInput({
              kind: "mcp",
              source: ref.source,
              pluginId: candidate.pluginId,
            });
            if (prepared.id === ref.id && prepared.sha256 === ref.sha256) return true;
          } catch {
            // Try next matching candidate
          }
        }
        return false;
      }

      default:
        return false;
    }
  };

  const results = await Promise.all(
    toCheck.map(async (ref) => ({ id: ref.id, valid: await checkReference(ref) })),
  );
  const invalidIds = new Set(results.filter((entry) => !entry.valid).map((entry) => entry.id));
  return degradeInvalidQueryReferences(payload, invalidIds);
}
