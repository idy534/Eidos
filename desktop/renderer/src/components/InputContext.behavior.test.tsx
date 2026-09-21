import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { InputContextProvider, InputDropZone, InputReferenceCards, useInputContext } from "./InputContext.js";
import type { EidosRuntimeAPI } from "../contracts.js";
import type { InputReference } from "../../../shared/input-context.js";

const fileReference: InputReference = {
  id: "a".repeat(64),
  kind: "file",
  label: "a.txt",
  source: "/workspace/a.txt",
  sha256: "b".repeat(64),
  status: "content",
  size: 1,
};
const historyReference: InputReference = {
  ...fileReference,
  id: "c".repeat(64),
  kind: "history",
  label: "Previous conversation",
  source: "session-history",
};
const runtimeDescriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

describe("InputContext behavior", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => {
    if (runtimeDescriptor) Object.defineProperty(window, "eidosRuntime", runtimeDescriptor);
    else delete (window as Partial<Window>).eidosRuntime;
  });

  function setupRuntime(overrides: Record<string, unknown> = {}) {
    const api = {
      prepareInput: vi.fn().mockResolvedValue(fileReference),
      inputPathForFile: vi.fn().mockReturnValue("/workspace/a.txt"),
      onInputQuote: vi.fn().mockReturnValue(vi.fn()),
      pasteInputImage: vi.fn().mockResolvedValue(fileReference),
      showItemInFolder: vi.fn().mockResolvedValue(undefined),
      ...overrides,
    } as unknown as EidosRuntimeAPI;
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      writable: true,
      value: api,
    });
    return api;
  }

  function Actions() {
    const context = useInputContext();
    return (
      <>
        <button type="button" onClick={() => void context?.add({ kind: "excerpt", source: "selection", text: "quote" })}>
          add
        </button>
        <button type="button" onClick={() => void context?.paths(["/workspace/a.txt", "/workspace/b.txt"])}>
          paths
        </button>
        <span>{context?.error}</span>
      </>
    );
  }

  function provider(children: React.ReactNode, options: { ready?: boolean; onAdd?: ReturnType<typeof vi.fn> } = {}) {
    return (
      <InputContextProvider
        sessionId="session-1"
        workspaceRoot="/workspace"
        ready={options.ready ?? true}
        onAdd={options.onAdd ?? vi.fn()}
        onSettings={vi.fn()}
        onNavigateToSession={vi.fn()}
      >
        {children}
      </InputContextProvider>
    );
  }

  it("keeps accepted paths when a later path fails", async () => {
    const onAdd = vi.fn();
    const api = setupRuntime({
      prepareInput: vi.fn()
        .mockResolvedValueOnce(fileReference)
        .mockRejectedValueOnce(new Error("second path failed")),
    });

    render(provider(<Actions />, { onAdd }));
    fireEvent.click(screen.getByRole("button", { name: "paths" }));

    await waitFor(() => expect(onAdd).toHaveBeenCalledWith("session-1", fileReference));
    expect(onAdd).toHaveBeenCalledTimes(1);
    expect(api.prepareInput).toHaveBeenCalledTimes(2);
    expect(screen.getByText("操作失败，请查看 Runtime 日志。")).toBeInTheDocument();
  });

  it("blocks additions until draft hydration is ready", async () => {
    const api = setupRuntime();
    render(provider(<Actions />, { ready: false }));

    fireEvent.click(screen.getByRole("button", { name: "add" }));

    await waitFor(() => expect(screen.getByText("草稿尚未恢复，请稍后再添加引用。")).toBeInTheDocument());
    expect(api.prepareInput).not.toHaveBeenCalled();
  });

  it("maps dropped files and shows the draft-only drop affordance", async () => {
    const onAdd = vi.fn();
    const api = setupRuntime();
    const { container } = render(provider(
      <InputDropZone><span>composer</span></InputDropZone>,
      { onAdd },
    ));
    const zone = container.querySelector(".input-drop-zone") as HTMLElement;
    const file = new File(["a"], "a.txt", { type: "text/plain" });
    const dataTransfer = { types: ["Files"], files: [file], dropEffect: "none" };

    fireEvent.dragEnter(zone, { dataTransfer });
    expect(screen.getByText("添加到当前草稿；文件不会自动发送")).toBeInTheDocument();
    fireEvent.drop(zone, { dataTransfer });

    await waitFor(() => expect(onAdd).toHaveBeenCalledWith("session-1", fileReference));
    expect(api.inputPathForFile).toHaveBeenCalledWith(file);
    expect(screen.queryByText("添加到当前草稿；文件不会自动发送")).not.toBeInTheDocument();
  });

  it("navigates to a referenced history session", () => {
    const navigate = vi.fn();
    setupRuntime();
    render(
      <InputContextProvider
        sessionId="session-1"
        workspaceRoot="/workspace"
        ready
        onAdd={vi.fn()}
        onSettings={vi.fn()}
        onNavigateToSession={navigate}
      >
        <InputReferenceCards references={[historyReference]} />
      </InputContextProvider>,
    );

    fireEvent.click(screen.getByTitle("跳转到该对话"));
    expect(navigate).toHaveBeenCalledWith("session-history");
  });
});
