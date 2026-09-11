import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Item, Run } from "../contracts.js";
import { ArtifactProvider } from "./ArtifactContext.js";
import {
  collectOutputArtifacts,
  OutputContent,
  projectTurnResults,
  TurnResults,
  useCompleteSessionItems,
} from "./TurnResults.js";

const run: Run = {
  id: "run-results",
  sessionId: "session-results",
  status: "succeeded",
  modelId: "deepseek-v4-flash",
  modelStepCount: 1,
  createdAt: 1,
  updatedAt: 2,
};

function diff(path: string, oldValue = "old", newValue = "new"): string {
  return [
    `diff --git a/${path} b/${path}`,
    `--- a/${path}`,
    `+++ b/${path}`,
    "@@ -1 +1 @@",
    `-${oldValue}`,
    `+${newValue}`,
    "",
  ].join("\n");
}

function changeItem(
  id: string,
  path: string,
  overrides: Partial<NonNullable<Item["toolCall"]>> = {},
): Item {
  return {
    id,
    sessionId: run.sessionId,
    runId: run.id,
    ordinal: Number(id.replace(/\D/g, "")) || 1,
    kind: "file_change",
    status: "completed",
    createdAt: 1,
    toolCall: {
      id: `tool-${id}`,
      itemId: id,
      modelStepIndex: 1,
      batchOrder: 0,
      providerCallId: `provider-${id}`,
      toolName: "apply_patch",
      status: "completed",
      startedAt: 1,
      completedAt: 2,
      changeDiff: diff(path),
      ...overrides,
    },
  };
}

afterEach(() => cleanup());

function PaginatedOutput({ items }: { items: Item[] }) {
  const result = useCompleteSessionItems("session-results", items, "older-cursor");
  return <OutputContent artifacts={collectOutputArtifacts(result.items)} loading={result.loading} error={result.error} />;
}

describe("TurnResults", () => {
  it("keeps repeated paths honest and exposes persisted artifacts", () => {
    const items = [
      changeItem("item-1", "src/index.ts"),
      changeItem("item-2", "src/index.ts", { changeDiff: diff("src/index.ts", "new", "newer") }),
      changeItem("item-3", "index.html", {
        resultJson: JSON.stringify({ data: { created: ["docs/report.docx"] } }),
      }),
      changeItem("item-4", "gone.txt", {
        changeDiff: "",
        resultJson: JSON.stringify({ data: { deleted: ["gone.txt"] } }),
      }),
    ];

    const projection = projectTurnResults(items, run.id);

    expect(projection.textChanges.map((change) => change.path)).toEqual(["gone.txt", "index.html", "src/index.ts"]);
    expect(projection.statsKnown).toBe(false);
    expect(projection.textChanges.find((change) => change.path === "src/index.ts")).toMatchObject({
      additions: undefined,
      deletions: undefined,
    });
    expect(projection.artifacts).toMatchObject([
      { path: "docs/report.docx", kind: "docx", itemId: "item-3" },
      { path: "index.html", kind: "html", itemId: "item-3" },
    ]);
  });

  it("shows the compact multi-file card and routes review to its run and file", () => {
    const openReview = vi.fn();
    const items = [
      changeItem("item-1", "src/a.ts"),
      changeItem("item-2", "src/b.ts"),
      changeItem("item-3", "src/c.ts"),
      changeItem("item-4", "src/d.ts"),
    ];

    render(
      <ArtifactProvider
        value={{
          sessionId: run.sessionId,
          executionRoot: "/workspace",
          openFile: vi.fn(),
          openBrowser: vi.fn(),
          openReview,
        }}
      >
        <TurnResults run={run} items={items} />
      </ArtifactProvider>,
    );

    expect(screen.getByText("已编辑 4 个文件")).toBeInTheDocument();
    expect(screen.getByText("再显示 1 个文件")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "审核 src/d.ts" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "撤销" })).toBeDisabled();

    fireEvent.click(screen.getByText("再显示 1 个文件"));
    fireEvent.click(screen.getByRole("button", { name: "审核 src/d.ts" }));
    expect(openReview).toHaveBeenCalledWith({ runId: run.id, path: "src/d.ts", itemId: "item-4" });

    fireEvent.click(screen.getByRole("button", { name: "审核" }));
    expect(openReview).toHaveBeenLastCalledWith({ runId: run.id });
  });

  it("puts artifacts before text changes and uses the controlled DOCX opener", () => {
    const openFile = vi.fn();
    const openExternal = vi.fn();
    const item = changeItem("item-1", "index.html", {
      resultJson: JSON.stringify({ data: { created: ["docs/report.docx", "index.html"] } }),
    });

    render(
      <ArtifactProvider
        value={{
          sessionId: run.sessionId,
          executionRoot: "/workspace",
          openFile,
          openBrowser: vi.fn(),
          openExternal,
        }}
      >
        <TurnResults run={run} items={[item]} />
      </ArtifactProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "打开 report.docx" }));
    expect(openExternal).toHaveBeenCalledWith("docs/report.docx");
    expect(screen.getByText("文档 · DOCX")).toBeInTheDocument();
    expect(screen.getByText("网页 · HTML")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "审核 index.html" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "打开 正在读取页面标题…" }));
    expect(openFile).toHaveBeenCalledWith("index.html");
  });

  it("keeps the latest output record and removes deleted paths", () => {
    const first = changeItem("item-1", "report.pdf", {
      resultJson: JSON.stringify({ data: { created: ["report.pdf", "old.png"] } }),
    });
    const latest = changeItem("item-2", "report.pdf", {
      resultJson: JSON.stringify({ data: { modified: ["report.pdf"], deleted: ["old.png"] } }),
    });

    expect(collectOutputArtifacts([first, latest])).toMatchObject([
      { path: "report.pdf", itemId: "item-2" },
    ]);
  });

  it("pages older Session items before building the environment output list", async () => {
    const readSession = vi.fn().mockResolvedValue({
      items: [changeItem("item-9", "old.pdf", { resultJson: JSON.stringify({ data: { created: ["old.pdf"] } }) })],
      previousItemId: undefined,
    });
    const descriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
    Object.defineProperty(window, "eidosRuntime", { configurable: true, value: { readSession } });

    try {
      render(<PaginatedOutput items={[changeItem("item-10", "current.pdf", { resultJson: JSON.stringify({ data: { created: ["current.pdf"] } }) })]} />);
      await waitFor(() => expect(screen.getByRole("button", { name: "打开 old.pdf" })).toBeInTheDocument());
      expect(readSession).toHaveBeenCalledWith("session-results", { itemLimit: 200, beforeItemId: "older-cursor" });
    } finally {
      if (descriptor) Object.defineProperty(window, "eidosRuntime", descriptor);
      else delete (window as Partial<Window>).eidosRuntime;
    }
  });
});
