import { createTwoFilesPatch } from "diff";
import { parseDiff } from "react-diff-view";
import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Item, Run } from "./contracts.js";
import { projectNetFileDiff, type ParsedFileDiff } from "./persisted-file-diff.js";
import { projectTurnResults, TurnResults } from "./components/TurnResults.js";
import { TextReviewPanel } from "./components/TextReviewPanel.js";
import { LastTurnChanges } from "./components/LastTurnChanges.js";

const run: Run = { id: "net-run", sessionId: "net-session", status: "succeeded", modelStepCount: 2, createdAt: 1, updatedAt: 2 };

function patch(before: string, after: string, source = "a/file.md", target = "b/file.md"): ParsedFileDiff {
  return parseDiff(`diff --git ${source} ${target}\n${createTwoFilesPatch(source, target, before, after)}`)[0]!;
}

function item(file: ParsedFileDiff, ordinal: number): Item {
  const source = file.oldPath === "/dev/null" ? file.oldPath : `a/${file.oldPath}`;
  const target = file.newPath === "/dev/null" ? file.newPath : `b/${file.newPath}`;
  const changeDiff = `diff --git ${source} ${target}\n--- ${source}\n+++ ${target}\n`
    + file.hunks.map((hunk) => hunk.content + "\n" + hunk.changes.map((change) =>
      `${change.type === "insert" ? "+" : change.type === "delete" ? "-" : " "}${change.content}\n`,
    ).join("")).join("");
  return {
    id: `item-${ordinal}`, sessionId: run.sessionId, runId: run.id, ordinal,
    kind: "file_change", status: "completed", createdAt: ordinal,
    toolCall: {
      id: `call-${ordinal}`, itemId: `item-${ordinal}`, modelStepIndex: ordinal, batchOrder: 0,
      providerCallId: `provider-${ordinal}`, toolName: "apply_patch", status: "completed", startedAt: ordinal,
      changeDiff,
    },
  };
}

beforeEach(() => vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("saved patch net projection", () => {
  it("counts a five-line creation followed by a replacement as +5/-0 in both surfaces", () => {
    const initial = "# title\n\nfirst paragraph\n\nfirst draft\n";
    const final = "# title\n\nfirst paragraph\n\nfinal version\n";
    const files = [patch("", initial, "/dev/null"), patch(initial, final)];
    expect(projectNetFileDiff(files)).toMatchObject({ additions: 5, deletions: 0 });
    // Snapshot paging must not determine patch order.
    const items = [item(files[1]!, 2), item(files[0]!, 1)];
    expect(projectTurnResults(items, run.id)).toMatchObject({ additions: 5, deletions: 0, statsKnown: true });
    const { container } = render(<>
      <TurnResults run={run} items={items} />
      <TextReviewPanel sessionId={run.sessionId} runId={run.id} items={items} />
    </>);
    expect(container.querySelector(".turn-result__stats")).toHaveTextContent("+5-0");
    expect(container.querySelector(".git-review-stats")).toHaveTextContent("+5-0");
    expect(container.querySelector(".git-file-stats")).toHaveTextContent("+5-0");
    const codes = [...container.querySelectorAll(".diff-code")].map((code) => code.textContent).join("\n");
    expect(codes).toContain("final version");
    expect(codes).not.toContain("first draft");
    const legacy = render(<LastTurnChanges sessionId={run.sessionId} runId={run.id} items={items} disabled={false} />);
    expect(legacy.container.querySelector(".git-file-stats")).toHaveTextContent("+5-0");
  });

  it("counts repeated replacements of an existing line once", () => {
    expect(projectNetFileDiff([patch("old\n", "new\n"), patch("new\n", "newest\n")]))
      .toMatchObject({ additions: 1, deletions: 1 });
  });

  it("cancels a replacement reverted to its original content", () => {
    expect(projectNetFileDiff([patch("old\n", "new\n"), patch("new\n", "old\n")]))
      .toMatchObject({ additions: 0, deletions: 0 });
  });

  it("cancels a file created and deleted within the same scope", () => {
    expect(projectNetFileDiff([patch("", "new\n", "/dev/null"), patch("new\n", "", "a/file.md", "/dev/null")]))
      .toMatchObject({ additions: 0, deletions: 0, file: { oldPath: "file.md" } });
  });

  it("compares deletion and recreation with the original file", () => {
    expect(projectNetFileDiff([
      patch("same\nold\n", "", "a/file.md", "/dev/null"), patch("", "same\nnew\n", "/dev/null"),
    ])).toMatchObject({ additions: 1, deletions: 1 });
  });

  it("keeps line coordinates across zero-length insertion hunks", () => {
    expect(projectNetFileDiff([patch("one\ntwo\n", "one\nadded\ntwo\n"), patch("one\nadded\ntwo\n", "one\nfinal\ntwo\n")]))
      .toMatchObject({ additions: 1, deletions: 0 });
  });

  it("combines disjoint sparse hunks without publishing invented context", () => {
    const original = Array.from({ length: 30 }, (_, i) => `line ${i + 1}`).join("\n") + "\n";
    const first = original.replace("line 1\n", "first\n");
    const final = first.replace("line 30\n", "last\n");
    const result = projectNetFileDiff([patch(original, first), patch(first, final)]);
    expect(result).toMatchObject({ additions: 2, deletions: 2 });
    expect(JSON.stringify(result)).not.toContain("Eidos:");
    expect(result?.file.hunks).toHaveLength(2);
  });

  it("retains a creation's history when the file is renamed", () => {
    const items = [item(patch("", "draft\n", "/dev/null", "b/old.md"), 1), item(patch("draft\n", "final\n", "a/old.md", "b/new.md"), 2)];
    expect(projectTurnResults(items, run.id).textChanges).toMatchObject([
      { path: "new.md", additions: 1, deletions: 0 },
    ]);
  });

  it("does not sum a mismatched or missing patch chain", () => {
    expect(projectNetFileDiff([patch("old\n", "new\n"), patch("different\n", "final\n")])).toBeUndefined();
    expect(projectNetFileDiff([patch("old\n", "new\n"), undefined, patch("new\n", "final\n")])).toBeUndefined();
    const items = [item(patch("old\n", "new\n"), 1), item(patch("different\n", "final\n"), 2)];
    expect(projectTurnResults(items, run.id).statsKnown).toBe(false);
    const { container } = render(<TextReviewPanel sessionId={run.sessionId} runId={run.id} items={items} />);
    expect(container.querySelector(".git-review-stats")).toHaveTextContent("行数未完整统计");
  });

  it("rejects malformed hunks and bounded-range overflow", () => {
    const file = patch("old\n", "new\n");
    const bad = { ...file, hunks: [{ ...file.hunks[0]!, content: "@@ -1,2 +1,2 @@" }] };
    expect(projectNetFileDiff([bad])).toBeUndefined();
    const distant = { ...file, hunks: [{ ...file.hunks[0]!, content: "@@ -2147483647 +2147483647 @@" }] };
    expect(projectNetFileDiff([distant])).toBeUndefined();
  });

  it.each(["failed", "canceled", "in_progress"] as const)("keeps uncertain %s changes out of confirmed net statistics", (status) => {
    const uncertain = { ...item(patch("old\n", "new\n"), 1), status };
    expect(projectTurnResults([uncertain], run.id).statsKnown).toBe(false);
    const { container } = render(<TextReviewPanel sessionId={run.sessionId} runId={run.id} items={[uncertain]} />);
    expect(container.querySelector(".git-review-stats")).toHaveTextContent("行数未完整统计");
  });
});
