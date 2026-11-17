import { DEFAULT_FIND_OPTIONS, findText, type TextMatch } from "./find-text.js";
import { isAbsoluteFilesystemPath } from "../platform.js";

/** One search_text match as the Runtime reports it (column counts code points from 1). */
export interface SearchTextMatch {
  path: string;
  line?: number | undefined;
  column?: number | undefined;
  preview: string;
}

export interface SearchTextHitView {
  line?: number | undefined;
  column?: number | undefined;
  /** Original preview, for tooltips. */
  preview: string;
  /** Preview with indentation removed and, for long lines, the text before the match cut off. */
  text: string;
  leadingEllipsis: boolean;
  /** Ranges in `text` (UTF-16 offsets), sorted and non-overlapping. */
  highlights: TextMatch[];
}

export interface SearchTextFileGroup {
  path: string;
  name: string;
  /** Directory shown beside the name, relative to the shared root when there is one. */
  directory: string;
  hits: SearchTextHitView[];
}

export interface SearchTextResultsView {
  /** Directory shared by every absolute match path, shown once instead of on every file. */
  root: string;
  files: SearchTextFileGroup[];
  hitCount: number;
}

/** Cut long lines so a match past this many characters is still on screen. */
const WINDOW_THRESHOLD = 40;
/** Characters kept in front of the match after cutting. */
const WINDOW_LEAD = 20;

export function buildSearchTextResults(
  matches: readonly SearchTextMatch[],
  query: string,
  regex: boolean,
): SearchTextResultsView {
  const terms = searchHighlightTerms(query, regex);
  const byPath = new Map<string, SearchTextHitView[]>();
  for (const match of matches) {
    const hits = byPath.get(match.path) ?? [];
    hits.push(searchTextHitView(match, terms));
    byPath.set(match.path, hits);
  }
  const paths = [...byPath.keys()];
  const root = sharedRoot(paths);
  const files = paths.map((path) => {
    const relative = root ? path.slice(root.length + 1) : path;
    const split = splitDirectory(relative);
    return { path, name: split.name, directory: split.directory, hits: byPath.get(path)! };
  });
  return { root, files, hitCount: matches.length };
}

/**
 * The literal strings to highlight. Regular expressions are only highlighted when
 * they are literal alternatives (optionally anchored or with word boundaries):
 * running a model-written pattern in the Renderer could hang it on backtracking.
 */
export function searchHighlightTerms(query: string, regex: boolean): string[] {
  if (!query) return [];
  if (!regex) return [query];
  return regexLiteralAlternatives(query) ?? [];
}

function regexLiteralAlternatives(source: string): string[] | undefined {
  const alternatives: string[] = [];
  let current = "";
  for (let index = 0; index < source.length; index += 1) {
    const character = source[index]!;
    if (character === "\\") {
      const next = source[index + 1];
      if (next === undefined) return undefined;
      index += 1;
      if (next === "b") continue;
      if (/[A-Za-z0-9]/.test(next)) return undefined;
      current += next;
      continue;
    }
    if (character === "|") {
      if (!current) return undefined;
      alternatives.push(current);
      current = "";
      continue;
    }
    if (character === "^" && current === "") continue;
    if (character === "$" && (index === source.length - 1 || source[index + 1] === "|")) continue;
    if (".^$*+?()[]{}".includes(character)) return undefined;
    current += character;
  }
  if (!current) return undefined;
  alternatives.push(current);
  return alternatives;
}

function searchTextHitView(match: SearchTextMatch, terms: readonly string[]): SearchTextHitView {
  const preview = match.preview;
  const trimmedStart = preview.trimStart();
  const indent = preview.length - trimmedStart.length;
  let text = trimmedStart.trimEnd();
  let highlights = mergeRanges(terms.flatMap((term) => findText(text, term, DEFAULT_FIND_OPTIONS).matches));
  const columnOffset = match.column !== undefined
    ? codePointOffset(preview, match.column - 1) - indent
    : undefined;
  const anchor = highlights.find((range) => columnOffset !== undefined && range.end > columnOffset)?.start
    ?? (columnOffset !== undefined && columnOffset >= 0 && columnOffset < text.length ? columnOffset : highlights[0]?.start);
  let leadingEllipsis = false;
  if (anchor !== undefined && anchor > WINDOW_THRESHOLD) {
    let cut = anchor - WINDOW_LEAD;
    // Start at a word boundary rather than in the middle of an identifier.
    while (cut < anchor && /[\w$]/.test(text[cut - 1] ?? "")) cut += 1;
    while (cut < anchor && /\s/.test(text[cut] ?? "")) cut += 1;
    if (isLowSurrogate(text.charCodeAt(cut))) cut -= 1;
    text = text.slice(cut);
    highlights = highlights
      .filter((range) => range.end > cut)
      .map((range) => ({ start: Math.max(0, range.start - cut), end: range.end - cut }));
    leadingEllipsis = true;
  }
  return { line: match.line, column: match.column, preview, text, leadingEllipsis, highlights };
}

/** Converts a code-point count from the Runtime into a UTF-16 offset. */
function codePointOffset(text: string, codePoints: number): number {
  let offset = 0;
  let counted = 0;
  for (const character of text) {
    if (counted >= codePoints) break;
    offset += character.length;
    counted += 1;
  }
  return offset;
}

function isLowSurrogate(code: number): boolean {
  return code >= 0xdc00 && code <= 0xdfff;
}

function mergeRanges(ranges: TextMatch[]): TextMatch[] {
  const sorted = [...ranges].sort((left, right) => left.start - right.start || right.end - left.end);
  const merged: TextMatch[] = [];
  for (const range of sorted) {
    const last = merged.at(-1);
    if (last && range.start <= last.end) {
      last.end = Math.max(last.end, range.end);
    } else {
      merged.push({ ...range });
    }
  }
  return merged;
}

function pathSegments(path: string): string[] {
  return path.split(/[\\/]/);
}

/** The longest directory every path sits in, when all paths are absolute and it is deeper than a top-level folder. */
function sharedRoot(paths: readonly string[]): string {
  if (paths.length === 0 || !paths.every(isAbsoluteFilesystemPath)) return "";
  const directories = paths.map((path) => pathSegments(path).slice(0, -1));
  let length = Math.min(...directories.map((segments) => segments.length));
  for (let index = 0; index < length; index += 1) {
    const segment = directories[0]![index];
    if (directories.some((segments) => segments[index] !== segment)) {
      length = index;
      break;
    }
  }
  const shared = directories[0]!.slice(0, length);
  if (shared.filter(Boolean).length < 2) return "";
  return paths[0]!.slice(0, shared.join("/").length);
}

function splitDirectory(path: string): { name: string; directory: string } {
  const index = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  if (index < 0) return { name: path, directory: "" };
  return { name: path.slice(index + 1), directory: path.slice(0, index) };
}

/** How many files fit before the visible hits (each file capped at `perFile`) reach `budget`; always at least one. */
export function initialVisibleFileCount(
  files: readonly SearchTextFileGroup[],
  perFile: number,
  budget: number,
): number {
  let shown = 0;
  let count = 0;
  for (const file of files) {
    if (count > 0 && shown >= budget) break;
    shown += Math.min(file.hits.length, perFile);
    count += 1;
  }
  return count;
}
