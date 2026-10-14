import { applyPatch, formatPatch, parsePatch, structuredPatch } from "diff";
import { parseDiff } from "react-diff-view";
import type { Item } from "./contracts.js";

export type ParsedFileDiff = ReturnType<typeof parseDiff>[number];
export interface NetFileDiff {
  file: ParsedFileDiff;
  additions: number;
  deletions: number;
}

const MAX_LINES = 50_000;
const MAX_CHARACTERS = 4 * 1024 * 1024;
const MAX_PATCHES = 512;

export function committedFileDiff(item: Item, file: ParsedFileDiff | undefined): ParsedFileDiff | undefined {
  const call = item.toolCall;
  if (item.status !== "completed" || call?.status !== "completed" || call.approvalStatus === "pending") return undefined;
  try {
    const result: unknown = JSON.parse(call.resultJson ?? "{}");
    if (!result || typeof result !== "object" || Reflect.get(result, "outcome") === "error"
      || Reflect.get(result, "reconciliationRequired") === true) return undefined;
    return file;
  } catch {
    return undefined;
  }
}

function patchPath(path: string, side: "a" | "b"): string {
  return path === "/dev/null" ? path : `${side}/${path}`;
}

function patchText(file: ParsedFileDiff): string {
  const source = patchPath(file.oldPath, "a");
  const target = patchPath(file.newPath, "b");
  return `--- ${source}\n+++ ${target}\n` + file.hunks.map((hunk) => (
    hunk.content + "\n" + hunk.changes.map((change) => (
      `${change.type === "insert" ? "+" : change.type === "delete" ? "-" : " "}${change.content}\n`
    )).join("")
  )).join("");
}

function text(lines: readonly string[]): string {
  return lines.length ? lines.join("\n") + "\n" : "";
}

/** Project a bounded chain of saved patches. No current filesystem data is used. */
export function projectNetFileDiff(entries: readonly (ParsedFileDiff | undefined)[]): NetFileDiff | undefined {
  if (!entries.length || entries.length > MAX_PATCHES || entries.some((file) => !file || file.isBinary)) return undefined;
  const files = entries as readonly ParsedFileDiff[];
  const first = files[0]!;
  const last = files.at(-1)!;
  const created = first.type === "add" || first.oldPath === "/dev/null";
  const original: string[] = [];
  let current: string[] = [];
  const holes = new Map<string, number>();
  let complete = created;
  let exists = !created;
  let characters = 0;
  let previousPath: string | undefined;
  const deadline = performance.now() + 100;

  try {
    for (const file of files) {
      if (previousPath !== undefined && file.oldPath !== previousPath) return undefined;
      const raw = patchText(file);
      characters += raw.length;
      if (characters > MAX_CHARACTERS || raw.includes("\0") || performance.now() > deadline) return undefined;
      const patch = parsePatch(raw)[0];
      if (!patch) return undefined;
      const addsFile = file.type === "add" || file.oldPath === "/dev/null";
      if (addsFile === exists) return undefined;

      for (const hunk of patch.hunks) {
        // jsdiff normalizes zero-length ranges to the one-based insertion point.
        const start = hunk.oldStart - 1;
        const end = start + hunk.oldLines;
        if (start < 0 || end > MAX_LINES || !Number.isSafeInteger(end)) return undefined;
        if (complete && end > current.length) return undefined;
        // Unknown original lines remain stable internal identities. A later saved
        // hunk may reveal their text; it must not overwrite already known text.
        while (current.length < end) {
          if (original.length >= MAX_LINES) return undefined;
          const marker = `\0Eidos:${original.length}\0`;
          holes.set(marker, original.length);
          original.push(marker);
          current.push(marker);
        }
        let position = start;
        for (const line of hunk.lines) {
          if (line[0] !== " " && line[0] !== "-") continue;
          const expected = line.slice(1);
          const value = current[position];
          if (value === undefined) return undefined;
          const originalIndex = holes.get(value);
          if (originalIndex !== undefined) {
            original[originalIndex] = expected;
            current[position] = expected;
          } else if (value !== expected) {
            return undefined;
          }
          position += 1;
        }
      }

      const candidate = applyPatch(text(current), patch, { fuzzFactor: 0, autoConvertLineEndings: false });
      if (candidate === false || candidate.length > MAX_CHARACTERS) return undefined;
      current = candidate ? (candidate.endsWith("\n") ? candidate.slice(0, -1) : candidate).split("\n") : [];
      if (current.length > MAX_LINES) return undefined;
      exists = file.type !== "delete" && file.newPath !== "/dev/null";
      previousPath = file.newPath;
      if (!exists) {
        if (current.length) return undefined;
        complete = true;
      }
    }

    const source = created ? "/dev/null" : patchPath(first.oldPath, "a");
    const target = exists ? patchPath(last.newPath, "b") : "/dev/null";
    const net = structuredPatch(source, target, text(original), text(current), undefined, undefined, {
      context: 0, timeout: 50, maxEditLength: MAX_LINES,
    });
    if (!net || net.hunks.some((hunk) => hunk.lines.some((line) => line.includes("\0")))) return undefined;
    let additions = 0;
    let deletions = 0;
    for (const hunk of net.hunks) for (const line of hunk.lines) {
      if (line.startsWith("+")) additions += 1;
      if (line.startsWith("-")) deletions += 1;
    }
    const file = files.length === 1 ? first
      : parseDiff(`diff --git ${source} ${target}\n${formatPatch(net)}`)[0];
    if (!file) return undefined;
    // An add followed by a delete has no net lines, but retains its recorded path.
    return { file: source === "/dev/null" && target === "/dev/null" ? { ...last, hunks: [] } : file, additions, deletions };
  } catch {
    return undefined;
  }
}
