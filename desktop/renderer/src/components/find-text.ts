export interface FindOptions { caseSensitive: boolean; wholeWord: boolean; regex: boolean }
export interface TextMatch { start: number; end: number }
export const DEFAULT_FIND_OPTIONS: FindOptions = { caseSensitive: false, wholeWord: false, regex: false };

export function findText(text: string, query: string, options: FindOptions): { matches: TextMatch[]; truncated: boolean } {
  if (!query) return { matches: [], truncated: false };
  let source = "";
  const positions: { start: number; end: number }[] = [];
  let offset = 0;
  for (const character of text) {
    const folded = options.caseSensitive || options.regex ? character : character.toLowerCase();
    for (const unit of folded.split("")) {
      source += unit;
      positions.push({ start: offset, end: offset + character.length });
    }
    offset += character.length;
  }
  const needle = options.caseSensitive || options.regex ? query : Array.from(query, (c) => c.toLowerCase()).join("");
  const pattern = options.regex ? new RegExp(query, `gu${options.caseSensitive ? "" : "i"}`) : undefined;
  const matches: TextMatch[] = [];
  const word = (value: string) => /[\p{L}\p{N}\p{M}_]/u.test(value);
  let cursor = 0;
  while (cursor <= source.length) {
    const found = pattern?.exec(source);
    const start = pattern ? found?.index ?? -1 : source.indexOf(needle, cursor);
    if (start < 0) break;
    const length = pattern ? found![0].length : needle.length;
    cursor = start + Math.max(length, 1);
    if (pattern && !length) { cursor = start + ((source.codePointAt(start) ?? 0) > 0xffff ? 2 : 1); pattern.lastIndex = cursor; continue; }
    const end = start + length;
    if (options.wholeWord && (word(Array.from(source.slice(Math.max(0, start - 2), start)).at(-1) ?? "") || word(Array.from(source.slice(end, end + 2))[0] ?? ""))) continue;
    const originalStart = positions[start]?.start;
    const originalEnd = positions[end - 1]?.end;
    if (originalStart !== undefined && originalEnd !== undefined) matches.push({ start: originalStart, end: originalEnd });
    if (matches.length >= 2000) return { matches, truncated: true };
  }
  return { matches, truncated: false };
}

export function textNodes(root: HTMLElement): { text: string; nodes: { node: Text; start: number; end: number }[] } {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, { acceptNode: (node) =>
    node.parentElement?.closest('button, input, textarea, [aria-hidden="true"], .preview-line-number, .code-block-toolbar, .find-bar')
      ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT });
  const nodes: { node: Text; start: number; end: number }[] = [];
  let text = "";
  let previousBlock: Element | null = null;
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const block = node.parentElement?.closest("p,li,pre,tr,h1,h2,h3,h4,.preview-line") ?? root;
    if (previousBlock && block !== previousBlock) text += "\n";
    previousBlock = block;
    const start = text.length;
    text += node.textContent;
    nodes.push({ node: node as Text, start, end: text.length });
  }
  return { text, nodes };
}

export function highlightText(root: HTMLElement, query: string, occurrence = 0, name = "eidos-chat-find") {
  const content = textNodes(root);
  let normalized = "";
  const mapping: { start: number; end: number }[] = [];
  for (let index = 0; index < content.text.length; index++) {
    const character = content.text[index]!;
    if (/\s/u.test(character)) {
      if (normalized.endsWith(" ")) { mapping[mapping.length - 1]!.end = index + 1; continue; }
      normalized += " ";
    } else normalized += character;
    mapping.push({ start: index, end: index + 1 });
  }
  const { matches } = findText(normalized, query.replace(/\s+/gu, " ").trim(), DEFAULT_FIND_OPTIONS);
  return paintMatches(content.nodes, matches.map((match) => ({ start: mapping[match.start]!.start, end: mapping[match.end - 1]!.end })), occurrence, name);
}

export function paintMatches(nodes: ReturnType<typeof textNodes>["nodes"], matches: TextMatch[], active: number, name: string) {
  const ranges = matches.flatMap((match) => {
    const first = nodes.find((entry) => entry.end > match.start);
    const last = nodes.find((entry) => entry.end >= match.end);
    if (!first || !last) return [];
    const range = document.createRange();
    range.setStart(first.node, Math.max(0, match.start - first.start));
    range.setEnd(last.node, Math.min(last.node.length, match.end - last.start));
    return [range];
  });
  if (typeof Highlight !== "undefined" && CSS.highlights) {
    CSS.highlights.set(name, new Highlight(...ranges));
    CSS.highlights.set(`${name}-active`, new Highlight(...(ranges[active] ? [ranges[active]] : [])));
  }
  const element = ranges[active]?.startContainer.parentElement;
  if (element) {
    const viewport = element.closest<HTMLElement>(".feed, .workspace-preview-pane");
    if (viewport) {
      const rect = ranges[active]?.getBoundingClientRect?.() ?? element.getBoundingClientRect();
      viewport.scrollTop += rect.top - viewport.getBoundingClientRect().top - viewport.clientHeight / 2;
      const pre = element.closest("pre");
      if (pre && rect.right > pre.getBoundingClientRect().right) pre.scrollLeft += rect.right - pre.getBoundingClientRect().right;
    }
  }
  return ranges.length;
}

export function clearHighlights(name: string) {
  if (typeof CSS !== "undefined" && CSS.highlights) { CSS.highlights.delete(name); CSS.highlights.delete(`${name}-active`); }
}
