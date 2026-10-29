import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Item, Run } from "../contracts.js";
import { DiffViewer, parseMultiFileDiff } from "./DiffViewer.js";
import { ExecutionFeed } from "./ExecutionFeed.js";
import { ToolTextView } from "./ToolTextView.js";

const path = "build/scripts/receipt.py";
const added = `diff --git /dev/null b/${path}\n--- /dev/null\n+++ b/${path}\n@@ -0,0 +1 @@\n+print('receipt')\n`;

describe("tool diff filenames", () => {
  afterEach(() => vi.unstubAllGlobals());

  it.each([
    [added, path],
    ["diff --git a/gone.py /dev/null\n--- a/gone.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n", "gone.py"],
    ["diff --git a/old.py b/new.py\n--- a/old.py\n+++ b/new.py\n@@ -1 +1 @@\n-old\n+new\n", "new.py"],
    ["--- /dev/null\n+++ b/docs/海洋 notes.md\n@@ -0,0 +1 @@\n+new\n", "docs/海洋 notes.md"],
  ])("uses the patch's real path for the title", (diff, expected) => {
    const files = parseMultiFileDiff(diff!);
    expect(files).toHaveLength(1);
    expect(files[0]!.filename).toBe(expected);
    expect(files[0]!.rawDiff).toBe(diff);
  });

  it("keeps multi-file titles paired with their respective open actions", () => {
    const openFile = vi.fn();
    render(<DiffViewer diff={added + added.replaceAll(path, "build/selftest.py")} onOpenFile={openFile} />);
    fireEvent.click(screen.getByRole("button", { name: "receipt.py" }));
    fireEvent.click(screen.getByRole("button", { name: "selftest.py" }));
    expect(openFile.mock.calls).toEqual([[path], ["build/selftest.py"]]);
    expect(screen.getAllByRole("button", { name: "复制代码差异" })).toHaveLength(2);
  });

  it.each(["apply_patch", "write_file"])("keeps only apply_patch's filename inside its diff header: %s", (toolName) => {
    const openFile = vi.fn();
    const run: Run = { id: "run", sessionId: "session", status: "running", modelStepCount: 1, createdAt: 1, updatedAt: 1 };
    const item: Item = {
      id: "edit", sessionId: run.sessionId, runId: run.id, ordinal: 1, kind: "file_change", status: "completed", createdAt: 1,
      toolCall: { id: "tool", itemId: "edit", modelStepIndex: 1, batchOrder: 0, providerCallId: "provider", toolName,
        status: "completed", startedAt: 1, changeDiff: added },
    };
    const { container } = render(<ExecutionFeed items={[item]} runs={[run]} approvals={[]}
      onOpenFile={openFile} />);
    container.querySelectorAll("details").forEach((details) => { details.open = true; });
    const tool = container.querySelector(".tool-item")!;
    expect(tool.querySelector(".diff-file-name")).toHaveTextContent("receipt.py");
    expect(tool.querySelector(".tool-file-list") !== null).toBe(toolName !== "apply_patch");
    const link = tool.querySelector(".diff-file-name-link") ?? tool.querySelector(".tool-file-link");
    expect(link).not.toBeNull();
    fireEvent.click(link!);
    expect(openFile).toHaveBeenCalledWith(path);
  });

  it("retains the header open action after loading a large patch page", async () => {
    const openFile = vi.fn();
    vi.stubGlobal("eidosRuntime", { readToolText: vi.fn().mockResolvedValue({
      content: added, nextOffset: added.length, totalCharacters: added.length, sha256: "a".repeat(64),
    }) });
    render(<ToolTextView sessionId="session" toolCallId="tool" field="diff" sha256={"a".repeat(64)} totalBytes={100_000} onOpenFile={openFile} />);
    fireEvent.click(screen.getByRole("button", { name: "查看完整补丁" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "receipt.py" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "receipt.py" }));
    expect(openFile).toHaveBeenCalledWith(path);
  });

  it("renders only filename in diff header when path is a full absolute path and shows full path in link tooltip on hover", async () => {
    const openFile = vi.fn();
    const fullPath = "/Users/xielei/Documents/AF/appforge-service/internal/app/workflow/version/service_test.go";
    const absDiff = `diff --git a/${fullPath} b/${fullPath}\n--- a/${fullPath}\n+++ b/${fullPath}\n@@ -0,0 +1 @@\n+package version\n`;
    const { container } = render(<DiffViewer diff={absDiff} onOpenFile={openFile} />);
    const button = screen.getByRole("button", { name: "service_test.go" });
    expect(button).toBeInTheDocument();
    expect(container.querySelector(".diff-file-name")).toHaveTextContent("service_test.go");
    expect(button).toHaveAttribute("title", `打开当前文件：${fullPath}`);

    fireEvent.mouseEnter(button);
    await waitFor(() => {
      const tooltip = screen.getByRole("tooltip");
      expect(tooltip).toBeInTheDocument();
      expect(tooltip).toHaveTextContent(`打开当前文件：${fullPath}`);
      expect(tooltip).toHaveClass("eidos-link-tooltip");
    });

    fireEvent.click(button);
    expect(openFile).toHaveBeenCalledWith(fullPath);
  });
});
