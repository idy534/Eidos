import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  type ClipboardEvent,
  type CompositionEvent,
  type KeyboardEvent,
} from "react";
import type { InputReference, InputReferenceKind } from "../../../shared/input-context.js";
import {
  canonicalizeInlineText,
  containsInlineReferenceToken,
  formatInlineReferencesAsPlainText,
  parseInlineSegments,
  type InlineSegment,
} from "../../../shared/inline-references.js";

const INLINE_CLIPBOARD_MIME = "application/x-eidos-inline-draft";
const SVG_NS = "http://www.w3.org/2000/svg";

export interface InlineRichEditorProps {
  id?: string | undefined;
  className?: string | undefined;
  value: string;
  inlineReferences: readonly InputReference[];
  placeholder?: string | undefined;
  disabled?: boolean | undefined;
  autoFocus?: boolean | undefined;
  maxLength?: number | undefined;
  role?: "textbox" | "combobox" | undefined;
  ariaLabel?: string | undefined;
  ariaLabelledBy?: string | undefined;
  ariaExpanded?: boolean | undefined;
  ariaControls?: string | undefined;
  ariaActiveDescendant?: string | undefined;
  pendingCaretOffset?: number | null | undefined;
  onPendingCaretConsumed?: (() => void) | undefined;
  onSelectionChange?: ((range: { start: number; end: number }) => void) | undefined;
  onChange: (nextValue: string, caretOffset: number) => void;
  onRemoveReference?: ((id: string) => void) | undefined;
  onActivateReference: (reference: InputReference) => void;
  onSubmit?: (() => void) | undefined;
  onEscape?: (() => void) | undefined;
  onPasteFiles?: ((files: File[]) => boolean) | undefined;
}

export function getInlineReferenceTitle(reference: InputReference): string {
  switch (reference.kind) {
    case "history":
      return "跳转到该对话";
    case "skill":
      return `查看技能详情：${reference.label}`;
    case "agent":
      return `查看智能体详情：${reference.label}`;
    case "mcp":
      return `查看 MCP 详情：${reference.label}`;
    case "plugin":
      return `查看插件详情：${reference.label}`;
    default:
      return reference.source;
  }
}

function createSvgNode(
  tag: string,
  attrs: Record<string, string>,
): SVGElement {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [key, val] of Object.entries(attrs)) {
    el.setAttribute(key, val);
  }
  return el;
}

function createInlineReferenceIconSvg(kind: InputReferenceKind): SVGElement {
  if (kind === "skill") {
    const svg = createSvgNode("svg", {
      viewBox: "0 0 24 24",
      width: "14",
      height: "14",
      fill: "none",
      stroke: "currentColor",
      "stroke-width": "2",
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
      "aria-hidden": "true",
    });
    svg.append(
      createSvgNode("path", { d: "M12 5v16" }),
      createSvgNode("path", { d: "m16 12 2 2 4-4" }),
      createSvgNode("path", {
        d: "M22 6V5a2 2 0 0 0-1.999-2L16 3.002A5 5 0 0 0 12 5a5 5 0 0 0-4-2H4a2 2 0 0 0-2 2v12a2 2 0 0 0 1.999 2H8a5 5 0 0 1 4 2 5 5 0 0 1 4-2h4.001A2 2 0 0 0 22 17v-1.344",
      }),
    );
    return svg;
  }
  if (kind === "agent") {
    const svg = createSvgNode("svg", {
      viewBox: "0 0 16 16",
      width: "14",
      height: "14",
      fill: "none",
      stroke: "currentColor",
      "stroke-width": "1.3",
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
      "aria-hidden": "true",
    });
    svg.append(
      createSvgNode("circle", { cx: "8", cy: "3.5", r: "1.5" }),
      createSvgNode("circle", { cx: "3.5", cy: "11.5", r: "1.5" }),
      createSvgNode("circle", { cx: "12.5", cy: "11.5", r: "1.5" }),
      createSvgNode("path", { d: "M2.6 9.8C1.8 6.2 4.6 3.2 6.5 2.5" }),
      createSvgNode("path", { d: "M9.5 2.5C11.4 3.2 14.2 6.2 13.4 9.8" }),
      createSvgNode("path", { d: "M5.2 13C7 14.2 9 14.2 10.8 13" }),
    );
    return svg;
  }
  if (kind === "mcp") {
    const svg = createSvgNode("svg", {
      viewBox: "0 0 16 16",
      width: "14",
      height: "14",
      fill: "none",
      "aria-hidden": "true",
    });
    svg.append(
      createSvgNode("rect", {
        x: "2.5",
        y: "3",
        width: "11",
        height: "4",
        rx: "1",
        stroke: "currentColor",
        "stroke-width": "1.3",
      }),
      createSvgNode("rect", {
        x: "2.5",
        y: "9",
        width: "11",
        height: "4",
        rx: "1",
        stroke: "currentColor",
        "stroke-width": "1.3",
      }),
      createSvgNode("circle", { cx: "5", cy: "5", r: ".75", fill: "currentColor" }),
      createSvgNode("circle", { cx: "5", cy: "11", r: ".75", fill: "currentColor" }),
    );
    return svg;
  }
  if (kind === "plugin") {
    const svg = createSvgNode("svg", {
      viewBox: "0 0 16 16",
      width: "14",
      height: "14",
      fill: "none",
      "aria-hidden": "true",
    });
    svg.append(
      createSvgNode("path", {
        d: "M6 3.5h1.5a1.5 1.5 0 0 1 3 0H12a1 1 0 0 1 1 1v2.5a1.5 1.5 0 0 1 0 3V12a1 1 0 0 1-1 1H9.5a1.5 1.5 0 0 1-3 0H4a1 1 0 0 1-1-1V9.5a1.5 1.5 0 0 1 0-3V4.5a1 1 0 0 1 1-1h2Z",
        stroke: "currentColor",
        "stroke-width": "1.3",
        "stroke-linejoin": "round",
      }),
    );
    return svg;
  }
  const svg = createSvgNode("svg", {
    viewBox: "0 0 16 16",
    width: "14",
    height: "14",
    fill: "none",
    "aria-hidden": "true",
  });
  svg.append(
    createSvgNode("path", {
      d: "M2.5 4.5A1.5 1.5 0 0 1 4 3h8a1.5 1.5 0 0 1 1.5 1.5v6A1.5 1.5 0 0 1 12 12H5.5L3 14V4.5Z",
      stroke: "currentColor",
      "stroke-width": "1.3",
      "stroke-linejoin": "round",
    }),
    createSvgNode("path", {
      d: "M5.5 6.5h5M5.5 9h3",
      stroke: "currentColor",
      "stroke-width": "1.2",
      "stroke-linecap": "round",
    }),
  );
  return svg;
}

function serializeNode(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) {
    return (node.nodeValue ?? "").replace(/\u200B/g, "");
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return "";

  const el = node as HTMLElement;
  if (el.dataset.inlineSentinel === "true") return "";
  if (el.dataset.inlineRefRaw) return el.dataset.inlineRefRaw;
  if (el.tagName === "BR") return "\n";

  let out = "";
  for (const child of Array.from(el.childNodes)) {
    out += serializeNode(child);
  }
  return out;
}

export function serializeInlineEditorDom(root: HTMLElement): string {
  let result = "";
  const children = Array.from(root.childNodes);
  for (let index = 0; index < children.length; index++) {
    const child = children[index]!;
    if (child.nodeType === Node.ELEMENT_NODE) {
      const el = child as HTMLElement;
      if (
        (el.tagName === "DIV" || el.tagName === "P")
        && index > 0
        && !result.endsWith("\n")
      ) {
        result += "\n";
      }
    }
    result += serializeNode(child);
  }
  return result;
}

function domPointToOffset(root: HTMLElement, container: Node, offset: number): number {
  let acc = 0;
  const children = Array.from(root.childNodes);

  if (container === root) {
    const bound = Math.max(0, Math.min(offset, children.length));
    for (let i = 0; i < bound; i++) {
      acc += serializeNode(children[i]!).length;
    }
    return acc;
  }

  for (const child of children) {
    if (child === container) {
      if (child.nodeType === Node.TEXT_NODE) {
        return acc + Math.max(0, Math.min(offset, (child.nodeValue ?? "").length));
      }
      const el = child as HTMLElement;
      if (el.dataset?.inlineSentinel === "true") return acc;
      if (el.dataset?.inlineRefRaw) {
        return acc + (offset > 0 ? el.dataset.inlineRefRaw.length : 0);
      }
      return acc + domPointToOffset(el, container, offset);
    }
    if (child.contains(container)) {
      const el = child as HTMLElement;
      if (el.dataset?.inlineSentinel === "true") return acc;
      if (el.dataset?.inlineRefRaw) {
        return acc + el.dataset.inlineRefRaw.length;
      }
      return acc + domPointToOffset(el, container, offset);
    }
    acc += serializeNode(child).length;
  }

  return acc;
}

export function getInlineEditorSelectionOffsets(
  root: HTMLElement,
): { start: number; end: number } | undefined {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0) return undefined;
  const anchorNode = selection.anchorNode;
  const focusNode = selection.focusNode;
  if (!anchorNode || !focusNode) return undefined;
  if (!root.contains(anchorNode) || !root.contains(focusNode)) return undefined;

  const a = domPointToOffset(root, anchorNode, selection.anchorOffset);
  const b = domPointToOffset(root, focusNode, selection.focusOffset);
  return {
    start: Math.min(a, b),
    end: Math.max(a, b),
  };
}

function offsetToDomPoint(
  root: HTMLElement,
  target: number,
): { node: Node; offset: number } {
  const clamped = Math.max(0, target);
  let acc = 0;
  const children = Array.from(root.childNodes);

  for (let i = 0; i < children.length; i++) {
    const child = children[i]!;
    const el = child.nodeType === Node.ELEMENT_NODE ? (child as HTMLElement) : undefined;
    if (el?.dataset.inlineSentinel === "true") {
      return { node: root, offset: i };
    }
    if (child.nodeType === Node.TEXT_NODE) {
      const len = (child.nodeValue ?? "").length;
      if (clamped <= acc + len) {
        return { node: child, offset: clamped - acc };
      }
      acc += len;
      continue;
    }
    if (el?.dataset.inlineRefRaw) {
      const rawLen = el.dataset.inlineRefRaw.length;
      if (clamped <= acc) {
        return { node: root, offset: i };
      }
      if (clamped <= acc + rawLen) {
        const next = children[i + 1];
        if (next && next.nodeType === Node.TEXT_NODE) {
          return { node: next, offset: 0 };
        }
        return { node: root, offset: i + 1 };
      }
      acc += rawLen;
      continue;
    }
    const len = serializeNode(child).length;
    if (clamped <= acc + len && el) {
      return offsetToDomPoint(el, clamped - acc);
    }
    acc += len;
  }

  const nonSentinelCount = children.filter(
    (node) => (node as HTMLElement).dataset?.inlineSentinel !== "true",
  ).length;
  const last = nonSentinelCount > 0 ? children[nonSentinelCount - 1] : undefined;
  if (last && last.nodeType === Node.TEXT_NODE) {
    return { node: last, offset: (last.nodeValue ?? "").length };
  }
  return { node: root, offset: nonSentinelCount };
}

export function setInlineEditorSelectionOffsets(
  root: HTMLElement,
  start: number,
  end: number = start,
): void {
  const selection = window.getSelection();
  if (!selection) return;
  const startPoint = offsetToDomPoint(root, start);
  const endPoint = end === start ? startPoint : offsetToDomPoint(root, end);
  const range = document.createRange();
  range.setStart(startPoint.node, startPoint.offset);
  range.setEnd(endPoint.node, endPoint.offset);
  selection.removeAllRanges();
  selection.addRange(range);
}

function domMatchesSegments(
  root: HTMLElement,
  canonicalValue: string,
  segments: readonly InlineSegment[],
): boolean {
  if (serializeInlineEditorDom(root) !== canonicalValue) return false;
  const expectedTrailingSentinel = canonicalValue.endsWith("\n");
  const children = Array.from(root.childNodes);
  const hasSentinel =
    children.length > 0
    && (children.at(-1) as HTMLElement).dataset?.inlineSentinel === "true";
  if (hasSentinel !== expectedTrailingSentinel) return false;

  const contentNodes = hasSentinel ? children.slice(0, -1) : children;
  if (contentNodes.length !== segments.length) return false;

  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i]!;
    const node = contentNodes[i]!;
    if (seg.type === "text") {
      if (node.nodeType !== Node.TEXT_NODE || node.nodeValue !== seg.text) return false;
    } else {
      if (node.nodeType !== Node.ELEMENT_NODE) return false;
      const el = node as HTMLElement;
      if (
        el.dataset.inlineRefId !== seg.reference.id
        || el.dataset.inlineRefRaw !== seg.raw
        || el.dataset.inlineRefLabel !== seg.reference.label
        || el.dataset.inlineRefKind !== seg.reference.kind
      ) {
        return false;
      }
    }
  }
  return true;
}

export const InlineRichEditor = forwardRef<HTMLDivElement, InlineRichEditorProps>(
  function InlineRichEditor(
    {
      id,
      className,
      value,
      inlineReferences,
      placeholder,
      disabled = false,
      autoFocus = false,
      maxLength = 65536,
      role = "textbox",
      ariaLabel,
      ariaLabelledBy,
      ariaExpanded,
      ariaControls,
      ariaActiveDescendant,
      pendingCaretOffset,
      onPendingCaretConsumed,
      onSelectionChange,
      onChange,
      onRemoveReference,
      onActivateReference,
      onSubmit,
      onEscape,
      onPasteFiles,
    },
    forwardedRef,
  ) {
    const editorRef = useRef<HTMLDivElement>(null);
    const isComposingRef = useRef(false);
    const activateRef = useRef(onActivateReference);
    activateRef.current = onActivateReference;

    useImperativeHandle(forwardedRef, () => editorRef.current as HTMLDivElement);

    const canonicalValue = canonicalizeInlineText(value, inlineReferences);

    const buildEditorDom = useCallback(
      (root: HTMLElement, text: string, refs: readonly InputReference[]) => {
        const segments = parseInlineSegments(text, refs, { canonicalizeUnplaced: false });
        const fragment = document.createDocumentFragment();

        for (const seg of segments) {
          if (seg.type === "text") {
            fragment.appendChild(document.createTextNode(seg.text));
            continue;
          }
          const reference = seg.reference;
          const wrapper = document.createElement("span");
          wrapper.contentEditable = "false";
          wrapper.dataset.inlineRefId = reference.id;
          wrapper.dataset.inlineRefRaw = seg.raw;
          wrapper.dataset.inlineRefLabel = reference.label;
          wrapper.dataset.inlineRefKind = reference.kind;
          wrapper.className = `input-reference input-reference--composer input-reference--kind-${reference.kind} input-reference--inline-token`;

          const btn = document.createElement("button");
          btn.type = "button";
          btn.tabIndex = -1;
          btn.className = "input-reference__btn";
          btn.title = getInlineReferenceTitle(reference);
          btn.addEventListener("mousedown", (event) => {
            event.preventDefault();
          });
          btn.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();
            activateRef.current(reference);
          });

          const iconSpan = document.createElement("span");
          iconSpan.className = `input-reference__icon input-reference__icon--${reference.kind} input-reference__icon--cat-${reference.kind}`;
          iconSpan.appendChild(createInlineReferenceIconSvg(reference.kind));

          const labelSpan = document.createElement("span");
          labelSpan.className = "input-reference__label";
          labelSpan.textContent = reference.label;

          btn.append(iconSpan, labelSpan);
          wrapper.appendChild(btn);
          fragment.appendChild(wrapper);
        }

        if (text.endsWith("\n")) {
          const sentinel = document.createElement("br");
          sentinel.dataset.inlineSentinel = "true";
          fragment.appendChild(sentinel);
        }

        root.replaceChildren(fragment);
      },
      [],
    );

    useLayoutEffect(() => {
      const root = editorRef.current;
      if (!root || isComposingRef.current) return;

      const segments = parseInlineSegments(canonicalValue, inlineReferences, {
        canonicalizeUnplaced: false,
      });
      const wasFocused = document.activeElement === root;
      const previousSelection = wasFocused ? getInlineEditorSelectionOffsets(root) : undefined;

      if (!domMatchesSegments(root, canonicalValue, segments)) {
        buildEditorDom(root, canonicalValue, inlineReferences);
        if (pendingCaretOffset == null && wasFocused && previousSelection) {
          setInlineEditorSelectionOffsets(root, previousSelection.start, previousSelection.end);
        }
      }

      if (pendingCaretOffset != null && !disabled) {
        const clamped = Math.max(0, Math.min(canonicalValue.length, pendingCaretOffset));
        root.focus();
        setInlineEditorSelectionOffsets(root, clamped, clamped);
        onSelectionChange?.({ start: clamped, end: clamped });
        onPendingCaretConsumed?.();
      }
    }, [
      buildEditorDom,
      canonicalValue,
      disabled,
      inlineReferences,
      onPendingCaretConsumed,
      onSelectionChange,
      pendingCaretOffset,
    ]);

    useEffect(() => {
      if (!autoFocus || disabled) return;
      const root = editorRef.current;
      if (!root) return;
      root.focus();
      const end = canonicalValue.length;
      setInlineEditorSelectionOffsets(root, end, end);
      onSelectionChange?.({ start: end, end });
      // Only run on initial mount when autoFocus is requested.
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const reportSelection = useCallback(() => {
      const root = editorRef.current;
      if (!root) return;
      const offsets = getInlineEditorSelectionOffsets(root);
      if (offsets) onSelectionChange?.(offsets);
    }, [onSelectionChange]);

    useEffect(() => {
      const handleSelectionChange = () => {
        const root = editorRef.current;
        if (!root || document.activeElement !== root) return;
        reportSelection();
      };
      document.addEventListener("selectionchange", handleSelectionChange);
      return () => document.removeEventListener("selectionchange", handleSelectionChange);
    }, [reportSelection]);

    const applyTextReplacement = useCallback(
      (nextRawText: string, nextCaretOffset: number) => {
        const root = editorRef.current;
        const bounded = nextRawText.length > maxLength ? nextRawText.slice(0, maxLength) : nextRawText;
        const clampedCaret = Math.max(0, Math.min(bounded.length, nextCaretOffset));

        const remainingRefs = inlineReferences.filter((ref) =>
          containsInlineReferenceToken(bounded, ref.id),
        );
        const removedRefs = inlineReferences.filter(
          (ref) => !containsInlineReferenceToken(bounded, ref.id),
        );

        if (root && remainingRefs.length > 0) {
          buildEditorDom(root, bounded, remainingRefs);
          setInlineEditorSelectionOffsets(root, clampedCaret, clampedCaret);
        }

        onSelectionChange?.({ start: clampedCaret, end: clampedCaret });
        onChange(bounded, clampedCaret);
        for (const removed of removedRefs) {
          onRemoveReference?.(removed.id);
        }
      },
      [buildEditorDom, inlineReferences, maxLength, onChange, onRemoveReference, onSelectionChange],
    );

    const syncFromDomMutation = useCallback(() => {
      const root = editorRef.current;
      if (!root) return;
      let nextText = serializeInlineEditorDom(root);
      if (nextText.length > maxLength) {
        nextText = nextText.slice(0, maxLength);
      }
      const selection = getInlineEditorSelectionOffsets(root) ?? {
        start: nextText.length,
        end: nextText.length,
      };

      if (!isComposingRef.current) {
        const remainingRefs = inlineReferences.filter((ref) =>
          containsInlineReferenceToken(nextText, ref.id),
        );
        const removedRefs = inlineReferences.filter(
          (ref) => !containsInlineReferenceToken(nextText, ref.id),
        );
        const segments = parseInlineSegments(nextText, remainingRefs, {
          canonicalizeUnplaced: false,
        });
        if (remainingRefs.length > 0 && !domMatchesSegments(root, nextText, segments)) {
          buildEditorDom(root, nextText, remainingRefs);
          setInlineEditorSelectionOffsets(root, selection.end, selection.end);
        }
        onSelectionChange?.(selection);
        onChange(nextText, selection.end);
        for (const removed of removedRefs) {
          onRemoveReference?.(removed.id);
        }
      } else {
        onSelectionChange?.(selection);
        onChange(nextText, selection.end);
      }
    }, [buildEditorDom, inlineReferences, maxLength, onChange, onRemoveReference, onSelectionChange]);

    const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
      if (disabled) return;
      if (event.nativeEvent.isComposing || isComposingRef.current) return;

      if (event.key === "Escape" && onEscape) {
        event.preventDefault();
        onEscape();
        return;
      }

      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        onSubmit?.();
        return;
      }

      const root = editorRef.current;
      if (!root) return;
      const selection = getInlineEditorSelectionOffsets(root) ?? {
        start: canonicalValue.length,
        end: canonicalValue.length,
      };
      const segments = parseInlineSegments(canonicalValue, inlineReferences, {
        canonicalizeUnplaced: false,
      });

      if (event.key === "Enter" && event.shiftKey) {
        event.preventDefault();
        const nextText = `${canonicalValue.slice(0, selection.start)}\n${canonicalValue.slice(selection.end)}`;
        applyTextReplacement(nextText, selection.start + 1);
        return;
      }

      if (selection.start === selection.end) {
        const pos = selection.start;
        if (event.key === "Backspace") {
          const hit = segments.find(
            (seg): seg is Extract<InlineSegment, { type: "reference" }> =>
              seg.type === "reference" && seg.end === pos,
          );
          if (hit) {
            event.preventDefault();
            const nextText = `${canonicalValue.slice(0, hit.start)}${canonicalValue.slice(hit.end)}`;
            applyTextReplacement(nextText, hit.start);
            return;
          }
        }
        if (event.key === "Delete") {
          const hit = segments.find(
            (seg): seg is Extract<InlineSegment, { type: "reference" }> =>
              seg.type === "reference" && seg.start === pos,
          );
          if (hit) {
            event.preventDefault();
            const nextText = `${canonicalValue.slice(0, hit.start)}${canonicalValue.slice(hit.end)}`;
            applyTextReplacement(nextText, hit.start);
            return;
          }
        }
        if (event.key === "ArrowLeft" && !event.shiftKey && !event.metaKey && !event.altKey && !event.ctrlKey) {
          const hit = segments.find(
            (seg): seg is Extract<InlineSegment, { type: "reference" }> =>
              seg.type === "reference" && seg.end === pos,
          );
          if (hit) {
            event.preventDefault();
            setInlineEditorSelectionOffsets(root, hit.start, hit.start);
            onSelectionChange?.({ start: hit.start, end: hit.start });
            return;
          }
        }
        if (event.key === "ArrowRight" && !event.shiftKey && !event.metaKey && !event.altKey && !event.ctrlKey) {
          const hit = segments.find(
            (seg): seg is Extract<InlineSegment, { type: "reference" }> =>
              seg.type === "reference" && seg.start === pos,
          );
          if (hit) {
            event.preventDefault();
            setInlineEditorSelectionOffsets(root, hit.end, hit.end);
            onSelectionChange?.({ start: hit.end, end: hit.end });
            return;
          }
        }
      } else if (event.key === "Backspace" || event.key === "Delete") {
        event.preventDefault();
        const nextText = `${canonicalValue.slice(0, selection.start)}${canonicalValue.slice(selection.end)}`;
        applyTextReplacement(nextText, selection.start);
      }
    };

    const handleCopy = (event: ClipboardEvent<HTMLDivElement>) => {
      const root = editorRef.current;
      if (!root) return;
      const selection = getInlineEditorSelectionOffsets(root);
      if (!selection || selection.start === selection.end) return;
      const rawSlice = canonicalValue.slice(selection.start, selection.end);
      event.preventDefault();
      event.clipboardData.setData("text/plain", formatInlineReferencesAsPlainText(rawSlice));
      event.clipboardData.setData(INLINE_CLIPBOARD_MIME, rawSlice);
    };

    const handleCut = (event: ClipboardEvent<HTMLDivElement>) => {
      if (disabled) return;
      const root = editorRef.current;
      if (!root) return;
      const selection = getInlineEditorSelectionOffsets(root);
      if (!selection || selection.start === selection.end) return;
      const rawSlice = canonicalValue.slice(selection.start, selection.end);
      event.preventDefault();
      event.clipboardData.setData("text/plain", formatInlineReferencesAsPlainText(rawSlice));
      event.clipboardData.setData(INLINE_CLIPBOARD_MIME, rawSlice);
      const nextText = `${canonicalValue.slice(0, selection.start)}${canonicalValue.slice(selection.end)}`;
      applyTextReplacement(nextText, selection.start);
    };

    const handlePaste = (event: ClipboardEvent<HTMLDivElement>) => {
      if (disabled) return;
      const files = Array.from(event.clipboardData.files);
      if (files.length > 0 && onPasteFiles?.(files)) {
        event.preventDefault();
        return;
      }
      event.preventDefault();
      const pasted =
        event.clipboardData.getData(INLINE_CLIPBOARD_MIME)
        || event.clipboardData.getData("text/plain");
      if (!pasted) return;
      const root = editorRef.current;
      const selection = (root && getInlineEditorSelectionOffsets(root)) ?? {
        start: canonicalValue.length,
        end: canonicalValue.length,
      };
      const nextText = `${canonicalValue.slice(0, selection.start)}${pasted}${canonicalValue.slice(selection.end)}`;
      applyTextReplacement(nextText, selection.start + pasted.length);
    };

    return (
      <div
        ref={editorRef}
        id={id}
        className={`composer-rich-editor${className ? ` ${className}` : ""}`}
        contentEditable={!disabled}
        suppressContentEditableWarning
        role={role}
        aria-multiline="true"
        aria-label={ariaLabel}
        aria-labelledby={ariaLabelledBy}
        aria-disabled={disabled || undefined}
        aria-autocomplete={role === "combobox" ? "list" : undefined}
        aria-expanded={role === "combobox" ? ariaExpanded : undefined}
        aria-controls={role === "combobox" ? ariaControls : undefined}
        aria-activedescendant={role === "combobox" ? ariaActiveDescendant : undefined}
        tabIndex={disabled ? -1 : 0}
        data-placeholder={placeholder}
        data-empty={canonicalValue.length === 0 ? "true" : undefined}
        onInput={() => syncFromDomMutation()}
        onCompositionStart={() => {
          isComposingRef.current = true;
        }}
        onCompositionEnd={(_event: CompositionEvent<HTMLDivElement>) => {
          isComposingRef.current = false;
          syncFromDomMutation();
        }}
        onKeyDown={handleKeyDown}
        onKeyUp={reportSelection}
        onClick={reportSelection}
        onFocus={reportSelection}
        onCopy={handleCopy}
        onCut={handleCut}
        onPaste={handlePaste}
      />
    );
  },
);
