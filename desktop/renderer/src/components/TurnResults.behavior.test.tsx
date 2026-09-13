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

const declarationProvenance = {
  kind: "builtin" as const,
  sourceId: "eidos.declare-outputs",
  sourceVersion: "1",
  contentHash: "a".repeat(64),
};

interface DeclaredOutputInput {
  path: string;
  title?: string;
  sizeBytes?: number;
  version?: string;
}

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

function declaredItem(
  id: string,
  outputs: DeclaredOutputInput[],
  overrides: Partial<NonNullable<Item["toolCall"]>> = {},
): Item {
  return changeItem(id, outputs[0]?.path ?? "output.bin", {
    toolName: "declare_outputs",
    changeDiff: "",
    provenance: declarationProvenance,
    resultJson: JSON.stringify({
      outcome: "success",
      code: "ok",
      data: {
        executionRoot: "/workspace",
        outputs: outputs.map(({ path, title, sizeBytes = 1, version = "a".repeat(64) }) => ({
          path,
          sizeBytes,
          version,
          ...(title === undefined ? {} : { title }),
        })),
      },
      sideEffectsMayExist: false,
      reconciliationRequired: false,
    }),
    ...overrides,
  });
}

afterEach(() => cleanup());

function PaginatedOutput({ items }: { items: Item[] }) {
  const result = useCompleteSessionItems("session-results", items, "older-cursor");
  return <OutputContent artifacts={collectOutputArtifacts(result.items)} loading={result.loading} error={result.error} />;
}

describe("TurnResults", () => {
  it("opens image artifacts in a fullscreen preview and HTML artifacts in the browser", async () => {
    const openFile = vi.fn();
    const openBrowser = vi.fn();
    const descriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      value: {
        onNotification: vi.fn().mockReturnValue(vi.fn()),
        prepareWorkspacePreview: vi.fn().mockImplementation(async (_sessionId: string, path: string) => `eidos-preview://preview/${path}`),
        releaseWorkspacePreview: vi.fn().mockResolvedValue(undefined),
        readWorkspaceFilePreview: vi.fn().mockImplementation(async (_sessionId: string, path: string) => ({
          path,
          kind: path === "diagram.png" ? "image" : "html",
          sizeBytes: 20,
          truncated: false,
          version: "a".repeat(64),
          ...(path === "page.html" ? { content: "<title>产品预览</title>" } : {}),
        })),
      },
    });
    const items = [
      declaredItem("item-1", [{ path: "diagram.png" }]),
      declaredItem("item-2", [{ path: "page.html" }]),
    ];

    try {
      render(
        <ArtifactProvider
          value={{
            sessionId: run.sessionId,
            executionRoot: "/workspace",
            openFile,
            openBrowser,
          }}
        >
          <TurnResults run={run} items={items} />
        </ArtifactProvider>,
      );

      fireEvent.click(screen.getByRole("button", { name: "打开 diagram.png" }));
      expect(await screen.findByRole("dialog", { name: "diagram.png 图片预览" })).toBeInTheDocument();
      expect(openFile).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: "关闭图片预览" }));

      fireEvent.click(screen.getAllByRole("button", { name: "打开方式" })[0]!);
      fireEvent.click(screen.getByRole("menuitem", { name: "内置预览" }));
      expect(await screen.findByRole("dialog", { name: "diagram.png 图片预览" })).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "关闭图片预览" }));

      const htmlButton = await screen.findByRole("button", { name: "打开 page.html" });
      fireEvent.click(htmlButton);
      await waitFor(() => expect(openBrowser).toHaveBeenCalledWith("eidos-preview://preview/page.html"));
    } finally {
      if (descriptor) Object.defineProperty(window, "eidosRuntime", descriptor);
      else delete (window as Partial<Window>).eidosRuntime;
    }
  });

  it("keeps repeated paths honest and exposes persisted artifacts", () => {
    const items = [
      changeItem("item-1", "src/index.ts"),
      changeItem("item-2", "src/index.ts", { changeDiff: diff("src/index.ts", "new", "newer") }),
      declaredItem("item-3", [
        { path: "docs/report.docx" },
        { path: "index.html" },
      ], { changeDiff: diff("index.html") }),
      changeItem("item-4", "gone.txt", {
        changeDiff: "",
        resultJson: JSON.stringify({ data: { deleted: ["gone.txt"] } }),
      }),
    ];

    const projection = projectTurnResults(items, run.id);

    expect(projection.textChanges.map((change) => change.path)).toEqual(["gone.txt", "index.html", "src/index.ts"]);
    expect(projection.statsKnown).toBe(false);
    expect(projection.textChanges.find((change) => change.path === "src/index.ts")).toMatchObject({
      additions: 2,
      deletions: 2,
      cumulative: true,
    });
    expect(projection.artifacts).toMatchObject([
      { path: "docs/report.docx", kind: "document", itemId: "item-3" },
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

    fireEvent.click(screen.getByRole("button", { name: "已编辑 4 个文件" }));
    expect(openReview).toHaveBeenLastCalledWith({ runId: run.id });
  });

  it("shows the single-file card with single card styling and routes review to its run and file", () => {
    const openReview = vi.fn();
    const items = [changeItem("item-1", "src/single.ts")];

    const { container } = render(
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

    const card = container.querySelector(".turn-result-card--single");
    expect(card).toBeInTheDocument();
    expect(screen.getByText("已编辑 single.ts")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "已编辑 single.ts" }));
    expect(openReview).toHaveBeenCalledWith({ runId: run.id, path: "src/single.ts", itemId: "item-1" });
  });

  it("puts artifacts before text changes and uses the controlled DOCX opener", async () => {
    const openFile = vi.fn();
    const openExternal = vi.fn();
    const item = declaredItem("item-1", [
      { path: "docs/report.docx" },
      { path: "index.html" },
    ], { changeDiff: diff("index.html") });

    const descriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      value: {
        readWorkspaceFilePreview: vi.fn().mockResolvedValue({
          path: "docs/report.docx",
          kind: "unavailable",
          sizeBytes: 1,
          truncated: false,
          version: "a".repeat(64),
          reason: "unsupported",
        }),
      },
    });

    try {
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
      await waitFor(() => expect(openExternal).toHaveBeenCalledWith("docs/report.docx"));
      expect(screen.getByText("文档 · DOCX")).toBeInTheDocument();
      expect(screen.getByText("网页 · HTML")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "审核 index.html" })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "打开 index.html" })).toBeInTheDocument();
    } finally {
      if (descriptor) Object.defineProperty(window, "eidosRuntime", descriptor);
      else delete (window as Partial<Window>).eidosRuntime;
    }
  });

  it("keeps the latest declaration and preserves omitted output history", () => {
    const first = declaredItem("item-1", [
      { path: "report.pdf", title: "Draft" },
      { path: "old.png" },
    ]);
    const latest = declaredItem("item-2", [{ path: "report.pdf", title: "Final" }]);

    expect(collectOutputArtifacts([first, latest])).toMatchObject([
      { path: "report.pdf", itemId: "item-2", title: "Final" },
      { path: "old.png", itemId: "item-1" },
    ]);
  });

  it("pages older Session items before building the environment output list", async () => {
    const readSession = vi.fn().mockResolvedValue({
      items: [declaredItem("item-9", [{ path: "old.pdf" }])],
      previousItemId: undefined,
    });
    const descriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
    Object.defineProperty(window, "eidosRuntime", { configurable: true, value: { readSession } });

    try {
      render(<PaginatedOutput items={[declaredItem("item-10", [{ path: "current.pdf" }])]} />);
      await waitFor(() => expect(screen.getByRole("button", { name: "打开 old.pdf" })).toBeInTheDocument());
      expect(readSession).toHaveBeenCalledWith("session-results", { itemLimit: 200, beforeItemId: "older-cursor" });
    } finally {
      if (descriptor) Object.defineProperty(window, "eidosRuntime", descriptor);
      else delete (window as Partial<Window>).eidosRuntime;
    }
  });

  it("projects only successful builtin declarations and classifies every supported format", () => {
    const valid = declaredItem("item-valid", [
      { path: "report.DOCX" },
      { path: "slides.pptx" },
      { path: "data.XLSM" },
      { path: "table.tsv" },
      { path: "notes.md" },
    ]);
    const legacy = changeItem("item-legacy", "legacy.pptx", {
      changeDiff: "",
      resultJson: JSON.stringify({ data: { created: ["legacy.pptx"] } }),
    });
    const wrongSource = declaredItem("item-wrong-source", [{ path: "wrong.pdf" }], {
      provenance: { ...declarationProvenance, sourceId: "other-tool" },
    });
    const failed = declaredItem("item-failed", [{ path: "failed.xlsx" }]);
    failed.status = "failed";

    expect(projectTurnResults([valid, legacy, wrongSource, failed], run.id).artifacts).toMatchObject([
      { path: "data.XLSM", kind: "spreadsheet" },
      { path: "notes.md", kind: "file" },
      { path: "report.DOCX", kind: "document" },
      { path: "slides.pptx", kind: "presentation" },
      { path: "table.tsv", kind: "spreadsheet" },
    ]);
  });

  it("rejects the whole declaration batch when one output is malformed", () => {
    const invalid = declaredItem("item-invalid", [{ path: "report.md" }], {
      resultJson: JSON.stringify({
        outcome: "success",
        code: "ok",
        data: {
          executionRoot: "/workspace",
          outputs: [
            { path: "report.md", sizeBytes: 1, version: "a".repeat(64) },
            { path: "broken.md", sizeBytes: 1, version: "not-a-version" },
          ],
        },
        sideEffectsMayExist: false,
        reconciliationRequired: false,
      }),
    });

    expect(collectOutputArtifacts([invalid])).toEqual([]);
  });
});
